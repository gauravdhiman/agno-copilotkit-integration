# copilotkit_integration/agno_agent_adapter.py
import json
import traceback
import uuid
import copy
from typing import Any, AsyncGenerator, Dict, List, Optional, cast, Set

from copilotkit.agent import Agent as CopilotKitAgentBase
from copilotkit.action import ActionDict
from copilotkit.types import Message as CopilotKitMessage, MetaEvent
from copilotkit.protocol import (
    RuntimeEventTypes, RuntimeMetaEventName, RuntimeProtocolEvent,
    TextMessageStart, TextMessageContent, TextMessageEnd,
    NodeStarted, NodeFinished, RunStarted, RunFinished, RunError,
)
from copilotkit.runloop import (
    AgentStateMessage, agent_state_message, copilotkit_run, CopilotKitRunExecution, get_context_execution, queue_put
)

# Import Agno classes
from agno.agent.agent import Agent as AgnoAgentInternal
from agno.models.message import Message as AgnoMessage
from agno.run.response import RunResponse as AgnoRunResponse, RunEvent as AgnoRunEvent
from agno.exceptions import RunCancelledException
from agno.memory import AgentMemory # Legacy Memory
from agno.memory.v2 import Memory as MemoryV2 # New Memory

# Import Utilities
from .utils import (
    copilotkit_messages_to_agno,
    agno_messages_to_copilotkit,
    map_agno_chunk_to_copilotkit_protocol_events,
    filter_agno_state,
    CopilotKitAgnoState,
    CopilotKitStateProperties, # Import the properties model
    TimelineEvent # Import the new timeline event model
)
# Import playground operator helper (assuming it's moved or accessible)
# If this causes import issues, move format_tools to utils.py
from agno.playground.operator import format_tools


class AgnoAgentAdapter(CopilotKitAgentBase):
    """CopilotKit Adapter for running Agno Agents with streaming using runloop."""
    def __init__(
        self,
        agno_agent_instance: AgnoAgentInternal,
        user_id_property: str = "userId",
        name=None,
        description=None,
        **kwargs,
    ):
        super().__init__(
            name=name or agno_agent_instance.name or agno_agent_instance.__class__.__name__,
            description=description or agno_agent_instance.description,
            **kwargs,
        )
        self.agno_agent = agno_agent_instance
        self.user_id_property = user_id_property
        # Initialize the state object
        self.copilot_agno_state = CopilotKitAgnoState(
            messages=[],
            copilotkit=CopilotKitStateProperties(actions=[]),
            event_timeline=[],
            session_state={}
        )
        if self.agno_agent.storage:
            self.agno_agent.storage.mode = "agent"

    def update_copilot_agno_state(self) -> None:
        """
        Refreshes the `self.copilot_agno_state` object with the current state
        from the underlying `self.agno_agent`. This should be called before
        emitting state-related events (RunStarted, NodeStarted, NodeFinished, RunFinished).
        """
        print(f"[{self.name}] Updating CopilotKitAgnoState...") # Add logging

        # --- Preserve Timeline & Update State ---
        # Create new state while preserving existing timeline and copilotkit object
        new_state = CopilotKitAgnoState(
             messages=[],
             copilotkit=self.copilot_agno_state.copilotkit,
             event_timeline=self.copilot_agno_state.event_timeline,
            #  event_timeline=[copy.deepcopy(self.copilot_agno_state.event_timeline[-1])] if self.copilot_agno_state.event_timeline else [],
             session_state=copy.deepcopy(self.agno_agent.session_state or {})
        ) # Preserve existing timeline and copilotkit

        # --- Messages ---
        agno_messages: List[AgnoMessage] = []
        if self.agno_agent.memory:
            if isinstance(self.agno_agent.memory, MemoryV2):
                # Agno v2 Memory
                if self.agno_agent.session_id:
                    try:
                        agno_messages = self.agno_agent.memory.get_messages_for_session(self.agno_agent.session_id)
                    except Exception as e:
                        print(f"[{self.name}] Error getting V2 memory messages: {e}")
            elif isinstance(self.agno_agent.memory, AgentMemory):
                 # Legacy AgentMemory
                if self.agno_agent.memory.messages:
                    agno_messages = self.agno_agent.memory.messages
            else:
                print(f"[{self.name}] Warning: Unknown Agno memory type: {type(self.agno_agent.memory)}")

        new_state.messages = agno_messages_to_copilotkit(agno_messages)
        print(f"[{self.name}] State updated with {len(new_state.messages)} messages.") # Add logging

        # --- Filtered Session State & Extra Data ---
        # Include filtered versions if they contain useful, non-sensitive data for the frontend
        filtered_session_state = {}
        if self.agno_agent.session_state:
            try:
                filtered_session_state = filter_agno_state(self.agno_agent.session_state)
                if filtered_session_state:
                     print(f"[{self.name}] Adding filtered session_state to CopilotKit state: {list(filtered_session_state.keys())}") # Add logging
                     # Decide where to put this - maybe a custom field?
                     # For now, let's add it directly to the state object if needed later
                     # Or potentially merge into the main state dict (less clean)
                     # Option: Add a dedicated field to CopilotKitAgnoState if this is common
                     setattr(new_state, 'session_state', filtered_session_state) # Add as a dynamic attribute for now
            except Exception as e:
                print(f"[{self.name}] Error filtering Agno session_state: {e}")

        filtered_extra_data = {}
        if self.agno_agent.extra_data:
            try:
                filtered_extra_data = filter_agno_state(self.agno_agent.extra_data)
                if filtered_extra_data:
                     print(f"[{self.name}] Adding filtered extra_data to CopilotKit state: {list(filtered_extra_data.keys())}") # Add logging
                     # Similar decision as session_state
                     setattr(new_state, 'extra_data', filtered_extra_data) # Add as a dynamic attribute for now
            except Exception as e:
                print(f"[{self.name}] Error filtering Agno extra_data: {e}")


        # --- Assign the new state ---
        self.copilot_agno_state = new_state
        print(f"[{self.name}] CopilotKitAgnoState update complete.") # Add logging

    async def _process_agno_stream_and_queue(self, execution_details: CopilotKitRunExecution) -> None:
        """
        Coroutine that executes Agno agent's arun, maps events, and puts them on the queue.
        Manages TextMessageStart/End events correctly and prevents duplicate tool events.

        Note this method will be run in separate thread using `copilotkit_run()` and whatever
        events this will emit will be collected from that thread specific queue by `copilotkit_run()`
        """
        thread_id = execution_details["thread_id"]
        run_id = execution_details["run_id"]
        user_id = execution_details["user_id"] # Extract user_id
        copilotkit_messages = cast(List[CopilotKitMessage], execution_details["messages"])
        agent_name = execution_details["agent_name"]

        run_has_finished = False
        current_text_message_id: Optional[str] = None
        processed_tool_call_ids: Set[str] = set() # Track processed tool call results

        try:
            # --- Preparation ---
            agno_messages = copilotkit_messages_to_agno(copilotkit_messages)
            last_user_message = agno_messages[-1] if agno_messages and agno_messages[-1].role == "user" else None
            if last_user_message is None:
                raise ValueError("No user message found.")

            self.agno_agent.session_id = thread_id
            self.agno_agent.user_id = user_id # Set user_id on the agent
            # If session info is persistent, first load it
            if self.agno_agent.storage:
                self.agno_agent.load_session()

            # --- Initial State Update and Run Start ---
            self.update_copilot_agno_state() # Update state BEFORE RunStarted
            initial_state = self.copilot_agno_state.model_dump()
            await queue_put(RunStarted(type=RuntimeEventTypes.RUN_STARTED, state=initial_state), priority=True)
            await queue_put(NodeStarted(type=RuntimeEventTypes.NODE_STARTED, node_name="agno_run", state=initial_state), priority=True)

            # --- Execute Agno Stream ---
            if not hasattr(self.agno_agent, 'arun'):
                raise NotImplementedError("Agno agent instance must have an 'arun' method.")

            # Run the Agno agent and collect stream from it.
            agno_stream = await self.agno_agent.arun(
                message=last_user_message, stream=True, stream_intermediate_steps=True, messages=agno_messages
            )

            # Process each chunk in the stream
            async for agno_chunk in agno_stream:
                if not isinstance(agno_chunk, AgnoRunResponse):
                    continue # Skip non-response chunks if any

                # Check for cancellation signal from CopilotKit
                if get_context_execution().get("should_exit", False):
                    raise RunCancelledException("Run cancelled by CopilotKit.")

                # --- Map Agno Chunk to CopilotKit Protocol Events ---
                protocol_events = map_agno_chunk_to_copilotkit_protocol_events(agno_chunk)
                events_to_queue: List[RuntimeProtocolEvent] = []
                is_text_chunk = False

                for event in protocol_events:
                    event_type = event["type"]

                    #  --- Handle simple text message, send START if required (if new message). If subsequent chunk, dont send START
                    if event_type == RuntimeEventTypes.TEXT_MESSAGE_CONTENT:
                        is_text_chunk = True
                        if current_text_message_id is None:
                            # Start a new text message block if one isn't active
                            current_text_message_id = str(uuid.uuid4())
                            start_event = TextMessageStart(type=RuntimeEventTypes.TEXT_MESSAGE_START, messageId=current_text_message_id, parentMessageId=None) # type: ignore
                            events_to_queue.append(start_event)
                        event["messageId"] = current_text_message_id # Use the active message ID
                        events_to_queue.append(event)

                    # --- TODO: We need to handle ACTION_EXECUTION_START, ACTION_EXECUTION_ARGS and ACTION_EXECUTION_END also for frontend actions ---
                    # This would require defining a tool for Agno to somehow pass the control to frontend. Need to think on this.

                    
                    # --- Handle ACTION_EXECUTION_RESULT, this will only happen when server side action is executed (sign of tool call ended). For now
                    # this is not implemented in Agno as Agno have its own tools, but may be can keep it for future.
                    elif event_type == RuntimeEventTypes.ACTION_EXECUTION_RESULT:
                        # If a text message was ongoing, end it before emitting tool result
                        if current_text_message_id is not None:
                            end_event = TextMessageEnd(type=RuntimeEventTypes.TEXT_MESSAGE_END, messageId=current_text_message_id) # type: ignore
                            events_to_queue.append(end_event)
                            current_text_message_id = None # Reset message ID

                        tool_call_id = event["actionExecutionId"]
                        if tool_call_id not in processed_tool_call_ids:
                            # Find corresponding Start/Args/End events generated by the mapper
                            start_event = next((e for e in protocol_events if e["type"] == RuntimeEventTypes.ACTION_EXECUTION_START and e["actionExecutionId"] == tool_call_id), None)
                            args_event = next((e for e in protocol_events if e["type"] == RuntimeEventTypes.ACTION_EXECUTION_ARGS and e["actionExecutionId"] == tool_call_id), None)
                            end_event = next((e for e in protocol_events if e["type"] == RuntimeEventTypes.ACTION_EXECUTION_END and e["actionExecutionId"] == tool_call_id), None)

                            # ??? This needs to be re-looked, as per map_agno_chunk_to_copilotkit_protocol_events(), START, ARG and END wont happen together
                            if start_event and args_event and end_event:
                                events_to_queue.append(start_event)
                                events_to_queue.append(args_event)
                                events_to_queue.append(end_event)
                                events_to_queue.append(event) # Add the Result event itself
                                processed_tool_call_ids.add(tool_call_id)
                            else:
                                print(f"Warning: Could not find full sequence for tool call result {tool_call_id}")
                    # Ignore standalone Start, Args, End events here; they are added with the Result event

                # If this chunk was NOT text content, and a text message was active, end it.
                if not is_text_chunk and current_text_message_id is not None:
                    end_event = TextMessageEnd(type=RuntimeEventTypes.TEXT_MESSAGE_END, messageId=current_text_message_id) # type: ignore
                    events_to_queue.append(end_event)
                    current_text_message_id = None

                # Queue the collected protocol events for this chunk
                if events_to_queue:
                    await queue_put(*events_to_queue)

                # --- Handle Lifecycle Events based on Agno Event Type ---
                agno_event_type = agno_chunk.event
                event_summary = None # Initialize summary for this chunk
                event_details=""

                # Determine summary based on event type
                if agno_event_type == AgnoRunEvent.reasoning_started.value:
                    event_summary = "Agent started reasoning."
                elif agno_event_type == AgnoRunEvent.reasoning_completed.value:
                    # Extract reasoning details if available in the chunk's thinking, content or metadata
                    reasoning_details = getattr(agno_chunk, 'thinking', getattr(agno_chunk, 'content', None)) # Example: adjust based on actual chunk structure
                    if reasoning_details:
                        event_summary = f"Agent finished reasoning."
                        event_details = f"{reasoning_details}"
                    else:
                        event_summary = "Agent finished reasoning."
                # TODO: we are not yet handling reasoning_step type, we should do that may be in future
                # elif agno_event_type == AgnoRunEvent.reasoning_step.value:
                elif agno_event_type == AgnoRunEvent.tool_call_started.value:
                    tool_name = "unknown tool"
                    if agno_chunk.tools and len(agno_chunk.tools) > 0:
                        # Get tool name, make it more readable by replacing underscores with spaces
                        tool_name = agno_chunk.tools[-1].get('tool_name', tool_name).replace('_', ' ')
                    event_summary = f"Agent started calling tool: {tool_name}"
                elif agno_event_type == AgnoRunEvent.tool_call_completed.value:
                    tool_name = "unknown tool"
                    tool_result = ""
                    if self.agno_agent.session_state and 'last_tool_call_response' in self.agno_agent.session_state and self.agno_agent.session_state['last_tool_call_response']:
                        tool_result = str(self.agno_agent.session_state['last_tool_call_response'])
                    if agno_chunk.tools and len(agno_chunk.tools) > 0:
                        tool_name = agno_chunk.tools[0].get('tool_name', tool_name).replace('_', ' ')
                    event_summary = f"Agent finished calling tool: {tool_name}"
                    event_details=f"{tool_result}"
                elif agno_event_type == AgnoRunEvent.updating_memory.value:
                    event_summary = "Agent is updating its memory."
                elif agno_event_type == AgnoRunEvent.run_error.value:
                    error_content = str(getattr(agno_chunk, 'content', 'Unknown error'))
                    event_summary = f"Agent encountered an error."
                    event_details=f"Error Details: {error_content}"

                # Update state and emit lifecycle event IF a summary was generated
                if event_summary:
                    self.update_copilot_agno_state() # Refresh base state
                    # Append the new event to the timeline
                    self.copilot_agno_state.event_timeline.append(TimelineEvent(
                        event_type=agno_event_type, # Use .value for enum
                        event_summary=event_summary,
                        event_details=event_details
                    ))
                    current_state = self.copilot_agno_state.model_dump() # Get state WITH timeline

                    # Emit corresponding CopilotKit lifecycle events
                    # IMP note: NODE_STARTED and NODE_FINISHED gets translated to AGENT_STATE_MESSAGE internally in runloop.py - check handle_runtime_event()
                    #  so we dont need to explicitly create AGENT_STATE_MESSAGE messages.
                    if agno_event_type == AgnoRunEvent.reasoning_started.value:
                        await queue_put(NodeStarted(type=RuntimeEventTypes.NODE_STARTED, node_name="reasoning_started", state=current_state))
                    elif agno_event_type == AgnoRunEvent.reasoning_completed.value:
                        await queue_put(NodeFinished(type=RuntimeEventTypes.NODE_FINISHED, node_name="reasoning_ended", state=current_state))
                    # TODO: handle reasoning_step in future
                    # elif agno_event_type == AgnoRunEvent.reasoning_step.value:
                    elif agno_event_type == AgnoRunEvent.tool_call_started.value:
                        await queue_put(NodeStarted(type=RuntimeEventTypes.NODE_STARTED, node_name="tool_call_started", state=current_state))
                    elif agno_event_type == AgnoRunEvent.tool_call_completed.value:
                        await queue_put(NodeFinished(type=RuntimeEventTypes.NODE_FINISHED, node_name="tool_call_ended", state=current_state))
                    elif agno_event_type == AgnoRunEvent.updating_memory.value:
                        await queue_put(NodeStarted(type=RuntimeEventTypes.NODE_STARTED, node_name="memory_update", state=current_state))
                        await queue_put(NodeFinished(type=RuntimeEventTypes.NODE_FINISHED, node_name="memory_update", state=current_state))
                    elif agno_event_type == AgnoRunEvent.run_error.value:
                        await queue_put(RunError(type=RuntimeEventTypes.RUN_ERROR, error=str(agno_chunk.content)), priority=True)
                        # Also emit NodeFinished for the error state if needed, or rely on RunError/RunFinished
                        # await queue_put(NodeFinished(type=RuntimeEventTypes.NODE_FINISHED, node_name="error_state", state=current_state))

                # Handle RunError specifically for RunFinished signal
                if agno_event_type == AgnoRunEvent.run_error.value:
                    await queue_put(RunFinished(type=RuntimeEventTypes.RUN_FINISHED, state=current_state), priority=True)
                    run_has_finished = True # Mark as finished on error too
                    break # Stop processing stream on error


            # --- After Loop ---
            # Ensure any final open text message is closed
            if current_text_message_id is not None:
                await queue_put(TextMessageEnd(type=RuntimeEventTypes.TEXT_MESSAGE_END, messageId=current_text_message_id))

            # Update and emit final state only if run didn't end with an error
            if not run_has_finished:
                self.update_copilot_agno_state()
                final_state = self.copilot_agno_state.model_dump()
                await queue_put(NodeFinished(type=RuntimeEventTypes.NODE_FINISHED, node_name="agno_run", state=final_state))
                await queue_put(RunFinished(type=RuntimeEventTypes.RUN_FINISHED, state=final_state), priority=True)
                run_has_finished = True

        # --- Exception Handling ---
        except RunCancelledException:
            print(f"[{agent_name}] Run {run_id} cancelled.")
            if current_text_message_id is not None: await queue_put(TextMessageEnd(type=RuntimeEventTypes.TEXT_MESSAGE_END, messageId=current_text_message_id))
            # Update state before final RunFinished
            self.update_copilot_agno_state()
            final_state = self.copilot_agno_state.model_dump()
            await queue_put(NodeFinished(type=RuntimeEventTypes.NODE_FINISHED, node_name="agno_run", state=final_state))
            await queue_put(RunFinished(type=RuntimeEventTypes.RUN_FINISHED, state=final_state), priority=True)
            run_has_finished = True
        except Exception as e:
            print(f"[{agent_name}] Error during Agno agent streaming: {e}")
            traceback.print_exc()
            if current_text_message_id is not None: await queue_put(TextMessageEnd(type=RuntimeEventTypes.TEXT_MESSAGE_END, messageId=current_text_message_id))
            # Update state before RunError/RunFinished
            self.update_copilot_agno_state()
            final_state = self.copilot_agno_state.model_dump()
            await queue_put(RunError(type=RuntimeEventTypes.RUN_ERROR, error=str(e)), priority=True) # Send error content
            # Ensure RunFinished is always sent, even after an error
            await queue_put(RunFinished(type=RuntimeEventTypes.RUN_FINISHED, state=final_state), priority=True)
            run_has_finished = True

        # --- Finalization ---
        finally:
            # This block ensures RunFinished is sent if the stream ends unexpectedly without error or cancellation
            if not run_has_finished:
                 print(f"[{agent_name}] Agno stream ended unexpectedly for run {run_id}. Signalling RunFinished.")
                 if current_text_message_id is not None: await queue_put(TextMessageEnd(type=RuntimeEventTypes.TEXT_MESSAGE_END, messageId=current_text_message_id))
                 self.update_copilot_agno_state()
                 final_state = self.copilot_agno_state.model_dump()
                 await queue_put(RunFinished(type=RuntimeEventTypes.RUN_FINISHED, state=final_state), priority=True)

            # Persist final state if storage is configured
            if self.agno_agent.storage:
                 try:
                     self.agno_agent.write_to_storage(session_id=thread_id, user_id=user_id)
                     print(f"[{agent_name}] Final state saved for thread {thread_id}.")
                 except Exception as save_err:
                     print(f"[{agent_name}] Error saving final state for thread {thread_id}: {save_err}")
            print(f"[{agent_name}] Agno agent processing coroutine finished for run {run_id}")

    # execute, get_state, and dict_repr methods remain unchanged from the previous version
    def execute(
        self,
        *,
        state: dict, # CopilotKit state (passed by the runloop)
        messages: List[CopilotKitMessage],
        thread_id: str,
        actions: Optional[List[ActionDict]] = None, # CopilotKit frontend actions (if any)
        meta_events: Optional[List[MetaEvent]] = None,
        properties: Optional[Dict[str, Any]] = None, # Properties from <CopilotKit properties={...}>
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """Executes the Agno agent using CopilotKit runloop."""
        run_id = str(uuid.uuid4())
        # Extract user ID from properties passed from the frontend, default if not found
        user_id = (properties or {}).get(self.user_id_property, f"user_{thread_id}")

        should_exit = False
        if meta_events:
            for event in meta_events:
                # Check if the frontend requested an exit
                if event.get("name") == RuntimeMetaEventName.EXIT.value:
                    should_exit = True
                    print(f"[{self.name}] Exit requested via meta event for run {run_id}.")
                    break

        # Prepare the execution context for the runloop
        execution_details = CopilotKitRunExecution(
            thread_id=thread_id,
            agent_name=self.name,
            run_id=run_id,
            should_exit=should_exit, # Pass the exit signal
            node_name="start", # Initial node name
            is_finished=False,
            predict_state_configuration={}, # Agno doesn't use this yet
            predicted_state={},
            argument_buffer="",
            current_tool_call=None,
            state=state, # Initial CopilotKit state from the runloop
            # Pass necessary context to the processing coroutine
            messages=messages,
            actions=actions or [],
            user_id=user_id, # Pass the determined user ID
        )

        # TODO: May be a right place to define and regiter an Agno tool for client side action handling.
        # How about updating the system prompt to inform Agno for this special tool ?

        # copilotkit_run takes the COROUTINE function (_process_agno_stream_and_queue)
        # and returns an ASYNC GENERATOR that yields the processed events.
        return copilotkit_run(
            fn=lambda: self._process_agno_stream_and_queue(execution_details),
            execution=execution_details
        )

    async def get_state(self, *, thread_id: str) -> Dict[str, Any]:
        """Retrieves the persisted state of the Agno agent session from storage."""
        agent_name = self.agno_agent.name or self.__class__.__name__
        print(f"[{agent_name}] get_state called for thread_id: {thread_id}")

        if not self.agno_agent.storage:
            print(f"[{agent_name}] No storage configured for agent.")
            return {"threadId": thread_id, "threadExists": False, "state": {}, "messages": []}

        try:
            # Attempt to read the session using the storage mechanism
            agno_session: Optional[AgentSession] = self.agno_agent.storage.read(session_id=thread_id) # type: ignore

            if agno_session is None:
                print(f"[{agent_name}] Session not found in storage for thread_id: {thread_id}")
                return {"threadId": thread_id, "threadExists": False, "state": {}, "messages": []}

            print(f"[{agent_name}] Session found for thread_id: {thread_id}")

            # Extract session state and filter it
            agno_session_state = agno_session.session_data.get("session_state", {}) if agno_session.session_data else {}
            filtered_state = filter_agno_state(agno_session_state)

            # Extract and convert messages
            agno_messages: List[AgnoMessage] = []
            if agno_session.memory:
                 # Check for V2 Memory structure (dict of session IDs to lists of runs)
                if "runs" in agno_session.memory and isinstance(agno_session.memory["runs"], dict):
                    try:
                        # Re-hydrate the V2 Memory object to use its methods
                        temp_memory = MemoryV2(**agno_session.memory)
                        # Check if the specific session exists in the runs dict
                        if thread_id in temp_memory.runs:
                            agno_messages = temp_memory.get_messages_for_session(session_id=thread_id)
                        else:
                             print(f"[{agent_name}] Session ID {thread_id} not found within the 'runs' dict of V2 memory.")
                    except Exception as e:
                         print(f"[{agent_name}] Error parsing V2 memory from stored session: {e}")

                 # Check for legacy AgentMemory structure (list of messages)
                elif "messages" in agno_session.memory and isinstance(agno_session.memory["messages"], list):
                     try:
                         agno_messages = [AgnoMessage(**msg_dict) for msg_dict in agno_session.memory["messages"]]
                     except Exception as e:
                         print(f"[{agent_name}] Error parsing messages from stored session memory: {e}")


            copilotkit_messages = agno_messages_to_copilotkit(agno_messages)

            print(f"[{agent_name}] Returning state for thread_id: {thread_id} with {len(copilotkit_messages)} messages.")
            # Return the state in the format expected by CopilotKit frontend
            return {
                "threadId": thread_id,
                "threadExists": True,
                "state": filtered_state, # Send the filtered state
                "messages": copilotkit_messages # Send the converted messages
            }

        except Exception as e:
             print(f"Error getting Agno agent state for thread {thread_id}: {e}")
             traceback.print_exc()
             # Return an error state if something goes wrong
             return {"threadId": thread_id, "threadExists": False, "state": {"error": f"Failed to retrieve state: {str(e)}"}, "messages": []}

    def dict_repr(self) -> Dict[str, Any]:
        """Returns a dictionary representation of the adapter, including its type."""
        base = super().dict_repr()
        base['type'] = 'agno' # Indicate the adapter type
        return base