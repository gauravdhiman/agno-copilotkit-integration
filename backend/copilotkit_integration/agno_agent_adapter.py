# copilotkit_integration/agno_agent_adapter.py
import asyncio
import json
import traceback
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional, cast

from copilotkit.agent import Agent as CopilotKitAgentBase
from copilotkit.action import ActionDict
from copilotkit.types import Message as CopilotKitMessage, MetaEvent
from copilotkit.protocol import (
    RuntimeEventTypes, RuntimeMetaEventName, RuntimeProtocolEvent, # Added RuntimeProtocolEvent
    TextMessageStart, TextMessageContent, TextMessageEnd, # Import specific event types
    NodeStarted, NodeFinished, RunStarted, RunFinished, RunError,
    ActionExecutionStart, ActionExecutionArgs, ActionExecutionEnd, ActionExecutionResult # Import action types
)# Import LIFECYCLE events from runloop
from copilotkit.runloop import (
    copilotkit_run, CopilotKitRunExecution, get_context_execution, queue_put
)

# Import Agno classes
from agno.agent.agent import Agent as AgnoAgentInternal
from agno.models.message import Message as AgnoMessage
from agno.run.response import RunResponse as AgnoRunResponse, RunEvent as AgnoRunEvent
from agno.exceptions import RunCancelledException
from agno.memory import AgentMemory
# from agno.memory.v2 import Memory as MemoryV2

from .utils import (
    copilotkit_messages_to_agno,
    agno_messages_to_copilotkit,
    map_agno_chunk_to_copilotkit_protocol_events, # Use the protocol event mapper
    filter_agno_state,
)

class AgnoAgentAdapter(CopilotKitAgentBase):
    """CopilotKit Adapter for running Agno Agents with streaming using runloop."""
    def __init__(
        self,
        agno_agent_instance: AgnoAgentInternal,
        user_id_property: str = "userId",
        **kwargs,
    ):
        super().__init__(
            name=agno_agent_instance.name or agno_agent_instance.__class__.__name__,
            description=agno_agent_instance.description,
            **kwargs,
        )
        self.agno_agent = agno_agent_instance
        self.user_id_property = user_id_property
        if self.agno_agent.storage:
            self.agno_agent.storage.mode = "agent"

    async def _process_agno_stream_and_queue(self, execution_details: CopilotKitRunExecution) -> None:
        """
        Coroutine that executes Agno agent's arun, maps events, and puts them on the queue.
        Manages TextMessageStart/End events correctly.
        """
        thread_id = execution_details["thread_id"]
        run_id = execution_details["run_id"]
        user_id = execution_details["user_id"]
        copilotkit_messages = cast(List[CopilotKitMessage], execution_details["messages"])
        agent_name = execution_details["agent_name"]

        run_has_finished = False
        current_text_message_id: Optional[str] = None # Track the active text message ID

        try:
            # 1. Prepare Agno inputs & state
            agno_messages = copilotkit_messages_to_agno(copilotkit_messages)
            last_user_message = agno_messages[-1] if agno_messages and agno_messages[-1].role == "user" else None

            if last_user_message is None:
                raise ValueError("No user message found to run Agno agent.")

            self.agno_agent.session_id = thread_id
            self.agno_agent.user_id = user_id
            if self.agno_agent.storage:
                self.agno_agent.read_from_storage(session_id=thread_id, user_id=user_id)

            current_agno_state = self.agno_agent.session_state or {}

            # 2. Signal Run Start
            await queue_put(RunStarted(type=RuntimeEventTypes.RUN_STARTED, state=current_agno_state), priority=True)
            await queue_put(NodeStarted(type=RuntimeEventTypes.NODE_STARTED, node_name="agno_run", state=current_agno_state), priority=True)

            # 3. Execute Agno stream and process chunks
            if not hasattr(self.agno_agent, 'arun'):
                 raise NotImplementedError(f"Agno agent {self.agno_agent.name} does not have an arun method.")

            agno_stream = await self.agno_agent.arun(
                message=last_user_message,
                stream=True,
                stream_intermediate_steps=True
            )

            async for agno_chunk in agno_stream:
                if not isinstance(agno_chunk, AgnoRunResponse):
                    print(f"Warning: Received unexpected chunk type from Agno: {type(agno_chunk)}")
                    continue

                # Check for cancellation signal via context
                if get_context_execution().get("should_exit", False):
                    print(f"Cancellation requested for run {run_id}, stopping Agno iteration.")
                    raise RunCancelledException("Execution cancelled by frontend.")

                # Get latest state *before* processing chunk
                current_agno_state = self.agno_agent.session_state or {}
                agno_event_type = agno_chunk.event

                # --- Check if we need to end the current text message ---
                # End message if a non-text event occurs (tool call, state change, etc.)
                # or if the run completes/errors/cancels.
                is_text_content = agno_chunk.content and agno_event_type == AgnoRunEvent.run_response.value
                should_end_text = (not is_text_content) and current_text_message_id is not None

                if should_end_text:
                    await queue_put(TextMessageEnd(type=RuntimeEventTypes.TEXT_MESSAGE_END, messageId=current_text_message_id)) # type: ignore
                    current_text_message_id = None

                # --- Map chunk to PROTOCOL events ---
                protocol_events = map_agno_chunk_to_copilotkit_protocol_events(agno_chunk)
                text_content_event: Optional[TextMessageContent] = None
                other_protocol_events: List[RuntimeProtocolEvent] = []

                for event in protocol_events:
                    if event["type"] == RuntimeEventTypes.TEXT_MESSAGE_CONTENT:
                        text_content_event = cast(TextMessageContent, event)
                    else:
                        other_protocol_events.append(event)

                # --- Handle Text Content Streaming ---
                if text_content_event:
                    if current_text_message_id is None:
                        # Start a new text message
                        current_text_message_id = str(uuid.uuid4())
                        await queue_put(TextMessageStart(type=RuntimeEventTypes.TEXT_MESSAGE_START, messageId=current_text_message_id, parentMessageId=None))

                    # Send the content chunk for the *current* message
                    text_content_event["messageId"] = current_text_message_id # Replace placeholder
                    await queue_put(text_content_event)

                # --- Handle Other Protocol Events (Tools) ---
                if other_protocol_events:
                    await queue_put(*other_protocol_events)

                # --- Map chunk event to LIFECYCLE events ---
                node_name = "agno_step" # Default node name
                if agno_event_type == AgnoRunEvent.reasoning_started.value:
                     node_name = "reasoning"
                     await queue_put(NodeStarted(type=RuntimeEventTypes.NODE_STARTED, node_name=node_name, state=current_agno_state))
                elif agno_event_type == AgnoRunEvent.reasoning_completed.value:
                     await queue_put(NodeFinished(type=RuntimeEventTypes.NODE_FINISHED, node_name="reasoning", state=current_agno_state))
                elif agno_event_type == AgnoRunEvent.tool_call_started.value:
                     await queue_put(NodeStarted(type=RuntimeEventTypes.NODE_STARTED, node_name="tool_call", state=current_agno_state))
                elif agno_event_type == AgnoRunEvent.tool_call_completed.value:
                     await queue_put(NodeFinished(type=RuntimeEventTypes.NODE_FINISHED, node_name="tool_call", state=current_agno_state))
                elif agno_event_type == AgnoRunEvent.updating_memory.value:
                     await queue_put(NodeStarted(type=RuntimeEventTypes.NODE_STARTED, node_name="memory_update", state=current_agno_state))
                     await queue_put(NodeFinished(type=RuntimeEventTypes.NODE_FINISHED, node_name="memory_update", state=current_agno_state))
                elif agno_event_type == AgnoRunEvent.run_error.value:
                    print(f"Agno Error Event: {agno_chunk.content}")
                    raise Exception(str(agno_chunk.content))
                # RunCompleted is handled after the loop

            # --- After Loop ---
            # Ensure any open text message is closed
            if current_text_message_id is not None:
                await queue_put(TextMessageEnd(type=RuntimeEventTypes.TEXT_MESSAGE_END, messageId=current_text_message_id))
                current_text_message_id = None

            # Signal Normal Completion
            current_agno_state = self.agno_agent.session_state or {}
            await queue_put(NodeFinished(type=RuntimeEventTypes.NODE_FINISHED, node_name="agno_run", state=current_agno_state))
            await queue_put(RunFinished(type=RuntimeEventTypes.RUN_FINISHED, state=current_agno_state), priority=True)
            run_has_finished = True

        except RunCancelledException:
            print(f"Run {run_id} cancelled.")
            # Ensure any open text message is closed
            if current_text_message_id is not None:
                await queue_put(TextMessageEnd(type=RuntimeEventTypes.TEXT_MESSAGE_END, messageId=current_text_message_id))
            current_agno_state = self.agno_agent.session_state or {}
            await queue_put(NodeFinished(type=RuntimeEventTypes.NODE_FINISHED, node_name="agno_run", state=current_agno_state))
            await queue_put(RunFinished(type=RuntimeEventTypes.RUN_FINISHED, state=current_agno_state), priority=True)
            run_has_finished = True
        except Exception as e:
            print(f"Error during Agno agent streaming: {e}")
            traceback.print_exc()
             # Ensure any open text message is closed
            if current_text_message_id is not None:
                await queue_put(TextMessageEnd(type=RuntimeEventTypes.TEXT_MESSAGE_END, messageId=current_text_message_id))
            current_agno_state = self.agno_agent.session_state or {}
            await queue_put(RunError(type=RuntimeEventTypes.RUN_ERROR, error=e), priority=True)
            run_has_finished = True
        finally:
            # Ensure RunFinished or RunError is *always* sent if something weird happened
            if not run_has_finished:
                 print(f"Agno stream ended unexpectedly for run {run_id}. Signalling RunFinished.")
                 # Ensure any open text message is closed
                 if current_text_message_id is not None:
                    await queue_put(TextMessageEnd(type=RuntimeEventTypes.TEXT_MESSAGE_END, messageId=current_text_message_id))
                 current_agno_state = self.agno_agent.session_state or {}
                 await queue_put(RunFinished(type=RuntimeEventTypes.RUN_FINISHED, state=current_agno_state), priority=True)

            if self.agno_agent.storage:
                 try:
                     self.agno_agent.write_to_storage(session_id=thread_id, user_id=user_id)
                     print(f"[{agent_name}] Final state saved for thread {thread_id}.")
                 except Exception as save_err:
                     print(f"[{agent_name}] Error saving final state for thread {thread_id}: {save_err}")
            print(f"Agno agent processing coroutine finished for run {run_id}")

    def execute(
        self,
        *,
        state: dict, # CopilotKit state
        messages: List[CopilotKitMessage],
        thread_id: str,
        actions: Optional[List[ActionDict]] = None,
        meta_events: Optional[List[MetaEvent]] = None,
        properties: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """Executes the Agno agent using CopilotKit runloop."""
        run_id = str(uuid.uuid4())
        user_id = (properties or {}).get(self.user_id_property, "default_user")

        should_interrupt = False
        if meta_events:
            for event in meta_events:
                if event.get("name") == RuntimeMetaEventName.EXIT.value:
                    should_interrupt = True
                    break

        execution_details = CopilotKitRunExecution(
            thread_id=thread_id,
            agent_name=self.name,
            run_id=run_id,
            should_exit=should_interrupt,
            node_name="start",
            is_finished=False,
            predict_state_configuration={}, # TODO: Can Agno benefit from this?
            predicted_state={},
            argument_buffer="",
            current_tool_call=None,
            state=state, # Pass initial CopilotKit state
            # Pass necessary info for the processing function
            messages=messages,
            actions=actions or [],
            user_id=user_id,
        )

        # copilotkit_run takes the COROUTINE function and returns the ASYNC GENERATOR
        return copilotkit_run(
            fn=lambda: self._process_agno_stream_and_queue(execution_details),
            execution=execution_details
        )

    # get_state and dict_repr remain the same as the corrected version previously provided
    async def get_state(self, *, thread_id: str) -> Dict[str, Any]:
        """Retrieves the persisted state of the Agno agent session from storage."""
        agent_name = self.agno_agent.name or self.__class__.__name__
        print(f"[{agent_name}] get_state called for thread_id: {thread_id}")

        if not self.agno_agent.storage:
            print(f"[{agent_name}] No storage configured for agent.")
            return {"threadId": thread_id, "threadExists": False, "state": {}, "messages": []}

        try:
            agno_session: Optional[AgentSession] = self.agno_agent.storage.read(session_id=thread_id) # type: ignore
            if agno_session is None:
                print(f"[{agent_name}] Session not found in storage for thread_id: {thread_id}")
                return {"threadId": thread_id, "threadExists": False, "state": {}, "messages": []}

            print(f"[{agent_name}] Session found for thread_id: {thread_id}")

            agno_state = agno_session.session_data.get("session_state", {}) if agno_session.session_data else {}
            filtered_state = filter_agno_state(agno_state)

            agno_messages: List[AgnoMessage] = []
            if agno_session.memory:
                # *********** Enable below `if` block once Agno have v2 memory`
                # if "runs" in agno_session.memory and isinstance(agno_session.memory["runs"], dict) and isinstance(self.agno_agent.memory, MemoryV2):
                #      try:
                #          temp_memory = MemoryV2(**agno_session.memory)
                #          agno_messages = temp_memory.get_messages_for_session(session_id=thread_id)
                #      except Exception as e:
                #          print(f"[{agent_name}] Error parsing V2 memory messages from stored session: {e}")
                if "messages" in agno_session.memory and isinstance(agno_session.memory["messages"], list):
                     try:
                         agno_messages = [AgnoMessage(**msg_dict) for msg_dict in agno_session.memory["messages"]]
                     except Exception as e:
                         print(f"[{agent_name}] Error parsing messages from stored session memory: {e}")

            copilotkit_messages = agno_messages_to_copilotkit(agno_messages)

            print(f"[{agent_name}] Returning state for thread_id: {thread_id} with {len(copilotkit_messages)} messages.")
            return { "threadId": thread_id, "threadExists": True, "state": filtered_state, "messages": copilotkit_messages }

        except Exception as e:
             print(f"Error getting Agno agent state for thread {thread_id}: {e}")
             traceback.print_exc()
             return {"threadId": thread_id, "threadExists": False, "state": {"error": f"Failed to retrieve state: {str(e)}"}, "messages": []}

    def dict_repr(self) -> Dict[str, Any]:
        base = super().dict_repr()
        base['type'] = 'agno_agent'
        return base