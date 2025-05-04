# copilotkit_integration/agno_agent_adapter.py
""" 
This page attempts to display agent state and text messages chronologically, but it's not working perfectly. 
I think trying to force this is fighting CopilotKit's intended design and feels pretty hacky. It would be better 
to align with CopilotKit's recommended approach. They only keep the latest agent state message, overriding previous ones. 
This ensures the agent state message always appears at the top, followed by text messages, regardless of when they were triggered. 
As a design pattern, it's better to follow this than to break things by going against CopilotKit's intended behavior.
"""

import json
import traceback
import uuid
import copy
from textwrap import dedent
from typing import Any, AsyncGenerator, Dict, List, Optional, cast, Set, Callable, Awaitable

from agno.tools.function import Function
from copilotkit.agent import Agent as CopilotKitAgentBase
from copilotkit.action import ActionDict
from copilotkit.types import Message as CopilotKitMessage, MetaEvent
from copilotkit.protocol import (
    RuntimeEventTypes, RuntimeMetaEventName, RuntimeProtocolEvent,
    TextMessageStart, TextMessageEnd,
    NodeStarted, NodeFinished, RunStarted, RunFinished, RunError,
)
from copilotkit.runloop import (
    copilotkit_run, CopilotKitRunExecution, get_context_execution, queue_put
)

from agno.agent.agent import Agent as AgnoAgentInternal
from agno.models.message import Message as AgnoMessage
from agno.run.response import RunResponse as AgnoRunResponse, RunEvent as AgnoRunEvent
from agno.exceptions import RunCancelledException
from agno.memory import AgentMemory # Legacy Memory
from agno.memory.v2 import Memory as MemoryV2 # New Memory
from agno.utils.log import log_debug, log_error, log_exception, log_warning, log_info

from .utils import (
    copilotkit_messages_to_agno,
    agno_messages_to_copilotkit,
    map_agno_chunk_to_copilotkit_protocol_events,
    filter_agno_state,
    CopilotKitAgnoState,
    CopilotKitStateProperties, # Import the properties model
    TimelineEvent # Import the new timeline event model
)
from agno.playground.operator import format_tools


class AgnoAgentAdapter(CopilotKitAgentBase):
    """CopilotKit Adapter for running Agno Agents with streaming using runloop.
    """
    def __init__(
        self,
        agno_agent_instance: AgnoAgentInternal,
        user_id_property: str = "userId",       # Revisit: Should user id even be here ??
        name: str = None,
        description: str = None,
        enable_tool_call_logging: bool = False,
        markdown_agent_for_tool_call_response: AgnoAgentInternal = None,
        tool_call_frontend_action_name:str = "display_tool_call_details",
        eligible_events_for_timeline:List[AgnoRunEvent] = [
            AgnoRunEvent.reasoning_step,
            AgnoRunEvent.reasoning_completed,
            AgnoRunEvent.tool_call_completed,
            AgnoRunEvent.run_error,
        ],
        **kwargs,
    ):
        """
        Wrapper class over Agno Agent to bridge the communication between Agno and Copilotkit.

        Args:
            - agno_agent_instance (AgnoAgent): An instance of an Agno agent that will be wrapped.
            - markdown_agent_for_tool_call_response (AgnoAgent): If provided, this agent will be used for converting each tool call response (from agno_agent_instance) into markdown format for better readability 
                and presenation, defore emitting Action Messages. BE AWARE, this will normally lead to high latency as this additional agent call will be done for each tool call on main Agno agent.
                Defaults to `None`
            - user_id_property (str, optional): Property name used to store the user ID. Defaults to `userId`.
            - name (str, optional): Name for this agent adapter. This is what the frontend copilotkit should be aware of. If not provided, uses the Agno agent's name.
            - description (str, optional): Description for this agent adapter. If not provided, uses the Agno agent's description.
            - tool_call_frontend_action_name (str, optional): Name of the frontend copilotkit action (check useCopilotAction hook) to display tool call details.
                Defaults to `display_tool_call_details`. The dedault setting expects below copilotkit action hook to show tool calls, if not presents, events will be ignored.
                
                ```
                useCopilotAction({
                    name: "display_tool_call_details",
                    description: "Displays the details and results of a completed tool call in the chat.",
                    parameters: [
                        { name: "tool_call_summary", type: "string", description: "One liner summary of tool call.", required: false },
                        { name: "tool_name", type: "string", description: "Name of the tool called", required: true },
                        { name: "tool_args", type: "string", description: "Stringified arguments of the tool call", required: false },
                        { name: "tool_result", type: "string", description: "Result / outcome of tool call", required: true },
                    ],
                    render: ({ args, status }) => {},
                }
                ```

            - enable_tool_call_logging (bool, optional): This controls if the `ActionExecution***` messages (`ActionExecutionStart`, `ActionExecutionArgs`, `ActionExecutionEnd` and `ActionExecutionResult`) 
                should be emitted or not. Defaults to `False`.
            - eligible_events_for_timeline (List[AgnoRunEvent], optional): List of Agno agent's lifecycle events that should be included in the event timeline.
                Defaults to [reasoning_started, reasoning_step, reasoning_completed, tool_call_started, tool_call_completed, run_error].
            - **kwargs: Additional keyword arguments passed to the parent CopilotKitAgentBase class.
        """
        super().__init__(
            name=name or agno_agent_instance.name or agno_agent_instance.__class__.__name__,
            description=description or agno_agent_instance.description,
            **kwargs,
        )
        self.agno_agent = agno_agent_instance
        self.user_id_property = user_id_property
        self.markdown_agent_for_tool_call_response = markdown_agent_for_tool_call_response
        self.eligible_events_for_timeline = eligible_events_for_timeline
        self.tool_call_frontend_action_name = tool_call_frontend_action_name
        self.enable_tool_call_logging = enable_tool_call_logging
        self.is_markdown_agent_setup_done = False
        self.is_tool_call_logging_setup_done = False   # Determines if the tool call logging setup has been done or not
        
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
                        log_exception(f"[Copilotkit-Agno][{self.name}] Error getting V2 memory messages: {e}")
            elif isinstance(self.agno_agent.memory, AgentMemory):
                 # Legacy AgentMemory
                if self.agno_agent.memory.messages:
                    agno_messages = self.agno_agent.memory.messages
            else:
                log_warning(f"[Copilotkit-Agno][{self.name}] Unknown Agno memory type: {type(self.agno_agent.memory)}")

        new_state.messages = agno_messages_to_copilotkit(agno_messages)

        # --- Filtered Session State & Extra Data ---
        # Include filtered versions if they contain useful, non-sensitive data for the frontend
        filtered_session_state = {}
        if self.agno_agent.session_state:
            try:
                filtered_session_state = filter_agno_state(self.agno_agent.session_state)
                if filtered_session_state:
                    log_info(f"[Copilotkit-Agno][{self.name}] Adding filtered session_state to CopilotKit state: {list(filtered_session_state.keys())}")
                    # Decide where to put this - maybe a custom field?
                    # For now, let's add it directly to the state object if needed later
                    # Or potentially merge into the main state dict (less clean)
                    # Option: Add a dedicated field to CopilotKitAgnoState if this is common
                    setattr(new_state, 'session_state', filtered_session_state) # Add as a dynamic attribute for now
            except Exception as e:
                log_exception(f"[Copilotkit-Agno][{self.name}] Error filtering Agno session_state: {e}")

        filtered_extra_data = {}
        if self.agno_agent.extra_data:
            try:
                filtered_extra_data = filter_agno_state(self.agno_agent.extra_data)
                if filtered_extra_data:
                    log_info(f"[Copilotkit-Agno][{self.name}] Adding filtered extra_data to CopilotKit state: {list(filtered_extra_data.keys())}")
                    # Similar decision as session_state
                    setattr(new_state, 'extra_data', filtered_extra_data) # Add as a dynamic attribute for now
            except Exception as e:
                log_exception(f"[Copilotkit-Agno][{self.name}] Error filtering Agno extra_data: {e}")

        self.copilot_agno_state = new_state


    def _setup_markdown_agent(self):
        if not self.is_markdown_agent_setup_done:
            if not self.markdown_agent_for_tool_call_response.instructions:
                self.markdown_agent_for_tool_call_response.instructions = []
            self.markdown_agent_for_tool_call_response.instructions.append("Convert the given content to well formatted markdown content.")
            self.markdown_agent_for_tool_call_response.instructions.append("If there are any URLs, make them as link")
            self.markdown_agent_for_tool_call_response.instructions.append("If appropriate, buse bullet points")
            self.markdown_agent_for_tool_call_response.instructions.append("Make text to be highligted as bold")
            self.markdown_agent_for_tool_call_response.instructions.append("ENSURE NOTHING CHANGES OTHER THAN THE FORMAT. NEVER ALTER THE CONTENT - IT SHOULD BE WORD TO WORD SAME.")
            
            self.is_markdown_agent_setup_done = True
    

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

            # self.agno_agent.session_id = thread_id
            # self.agno_agent.user_id = user_id # Set user_id on the agent
            # If session info is persistent, first load it
            # if self.agno_agent.storage:
            #     self.agno_agent.load_session()

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
                protocol_event_dicts: List[Dict[str, Any]] = []
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
                    event_summary = f"Calling tool: {tool_name}"
                elif agno_event_type == AgnoRunEvent.tool_call_completed.value:
                    tool_call_id = str(uuid.uuid4())
                    tool_name = "unknown tool"
                    tool_args = {}
                    tool_result = ""
                    if agno_chunk.tools and len(agno_chunk.tools) > 0:
                        tool_name = agno_chunk.tools[-1].get('tool_name', tool_name).replace('_', ' ')
                        tool_name = str(tool_name).title()
                        tool_args = agno_chunk.tools[-1].get('tool_args', {})
                        if tool_args and isinstance(tool_args, dict):
                            tool_args = json.dumps(tool_args)
                        tool_call_id = agno_chunk.tools[-1].get('tool_call_id', tool_call_id)
                        tool_result = agno_chunk.tools[-1].get('content', "")
                        if tool_result and self.markdown_agent_for_tool_call_response:
                            self._setup_markdown_agent()
                            response_obj = self.markdown_agent_for_tool_call_response.run(dedent(f"""
                                Convert the below content to markdoen format:
                                
                                <Content>
                                {tool_result}
                                </Content>
                            """))
                            tool_result = response_obj.content if response_obj.content else ""

                    event_summary = f"Finished calling tool: {tool_name}"
                    event_details=f"{tool_result}"
                    
                    if self.enable_tool_call_logging:
                        # After every tool call completion, we should trigger frontend action to show tool details on UI. Its up to UI to catch it or not.
                        action_execution_events: List[Dict[str, Any]] = []
                        action_execution_events.append({"type": RuntimeEventTypes.ACTION_EXECUTION_START, "actionExecutionId": tool_call_id, "actionName": self.tool_call_frontend_action_name, "parentMessageId": None})
                        action_execution_events.append({"type": RuntimeEventTypes.ACTION_EXECUTION_ARGS, "actionExecutionId": tool_call_id, "args": json.dumps({"tool_call_summary": f"Called {tool_name}", "tool_name": tool_name, "tool_args": tool_args, "tool_result": tool_result})}) # Use dictionary directly
                        action_execution_events.append({"type": RuntimeEventTypes.ACTION_EXECUTION_END, "actionExecutionId": tool_call_id})
                        action_execution_events.append({"type": RuntimeEventTypes.ACTION_EXECUTION_RESULT, "actionExecutionId": tool_call_id, "actionName": self.tool_call_frontend_action_name, "result": json.dumps({'BackendResult': 'Execution done'})})
                        await queue_put(*action_execution_events)
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
                    if(agno_event_type in self.eligible_events_for_timeline):
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
            log_info(f"[Copilotkit-Agno][{agent_name}] Run {run_id} cancelled.")
            if current_text_message_id is not None: await queue_put(TextMessageEnd(type=RuntimeEventTypes.TEXT_MESSAGE_END, messageId=current_text_message_id))
            # Update state before final RunFinished
            self.update_copilot_agno_state()
            final_state = self.copilot_agno_state.model_dump()
            await queue_put(NodeFinished(type=RuntimeEventTypes.NODE_FINISHED, node_name="agno_run", state=final_state))
            await queue_put(RunFinished(type=RuntimeEventTypes.RUN_FINISHED, state=final_state), priority=True)
            run_has_finished = True
        except Exception as e:
            log_exception(f"[Copilotkit-Agno][{agent_name}] Error during Agno agent streaming: {e}")
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
                log_exception(f"[Copilotkit-Agno][{agent_name}] Agno stream ended unexpectedly for run {run_id}. Signalling RunFinished.")
                if current_text_message_id is not None: await queue_put(TextMessageEnd(type=RuntimeEventTypes.TEXT_MESSAGE_END, messageId=current_text_message_id))
                self.update_copilot_agno_state()
                final_state = self.copilot_agno_state.model_dump()
                await queue_put(RunFinished(type=RuntimeEventTypes.RUN_FINISHED, state=final_state), priority=True)

            # Persist final state if storage is configured
            if self.agno_agent.storage:
                 try:
                    thread_id = self.agno_agent.session_id or thread_id
                    user_id = self.agno_agent.user_id or user_id
                    self.agno_agent.write_to_storage(session_id=thread_id, user_id=user_id)
                    log_debug(f"[Copilotkit-Agno][{agent_name}] Final state saved for thread {thread_id}.")
                 except Exception as save_err:
                    log_exception(f"[Copilotkit-Agno][{agent_name}] Error saving final state for thread {thread_id}: {save_err}")
            log_debug(f"[Copilotkit-Agno][{agent_name}] Agno agent processing coroutine finished for run {run_id}")


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
        # clearup the timeline in state
        self.copilot_agno_state.event_timeline = []
        if meta_events:
            for event in meta_events:
                # Check if the frontend requested an exit
                if event.get("name") == RuntimeMetaEventName.EXIT.value:
                    should_exit = True
                    log_info(f"[Copilotkit-Agno][{self.name}] Exit requested via meta event for run {run_id}.")
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
        print(f">>>>>>>> ACTIONS >>>>> {actions}")

        # copilotkit_run takes the COROUTINE function (_process_agno_stream_and_queue)
        # and returns an ASYNC GENERATOR that yields the processed events.
        return copilotkit_run(
            fn=lambda: self._process_agno_stream_and_queue(execution_details),
            execution=execution_details
        )

    async def get_state(self, *, thread_id: str) -> Dict[str, Any]:
        """Retrieves the persisted state of the Agno agent session from storage."""
        agent_name = self.agno_agent.name or self.__class__.__name__
        log_debug(f"[Copilotkit-Agno][{self.name}] get_state() called for thread_id: {thread_id}.")

        if not self.agno_agent.storage:
            log_debug(f"[Copilotkit-Agno][{self.name}] No storage configured for agent.")
            return {"threadId": thread_id, "threadExists": False, "state": {}, "messages": []}

        try:
            # Attempt to read the session using the storage mechanism
            agno_session: Optional[AgentSession] = self.agno_agent.storage.read(session_id=thread_id) # type: ignore

            if agno_session is None:
                log_debug(f"[Copilotkit-Agno][{self.name}] Session not found in storage for thread_id: {thread_id}")
                return {"threadId": thread_id, "threadExists": False, "state": {}, "messages": []}

            log_debug(f"[Copilotkit-Agno][{self.name}] Session found for thread_id: {thread_id}")

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
                            log_debug(f"[Copilotkit-Agno][{self.name}] Session ID {thread_id} not found within the 'runs' dict of V2 memory.")
                    except Exception as e:
                        log_exception(f"[Copilotkit-Agno][{self.name}] Error parsing V2 memory from stored session: {e}")

                 # Check for legacy AgentMemory structure (list of messages)
                elif "messages" in agno_session.memory and isinstance(agno_session.memory["messages"], list):
                     try:
                         agno_messages = [AgnoMessage(**msg_dict) for msg_dict in agno_session.memory["messages"]]
                     except Exception as e:
                        log_exception(f"[Copilotkit-Agno][{self.name}] Error parsing messages from stored session memory: {e}")


            copilotkit_messages = agno_messages_to_copilotkit(agno_messages)

            log_debug(f"[Copilotkit-Agno][{self.name}] Returning state for thread_id: {thread_id} with {len(copilotkit_messages)} messages.")
            # Return the state in the format expected by CopilotKit frontend
            return {
                "threadId": thread_id,
                "threadExists": True,
                "state": filtered_state, # Send the filtered state
                "messages": copilotkit_messages # Send the converted messages
            }

        except Exception as e:
            log_exception(f"[Copilotkit-Agno][{self.name}] Error getting Agno agent state for thread {thread_id}: {e}")
            traceback.print_exc()
            # Return an error state if something goes wrong
            return {"threadId": thread_id, "threadExists": False, "state": {"error": f"Failed to retrieve state: {str(e)}"}, "messages": []}

    def dict_repr(self) -> Dict[str, Any]:
        """Returns a dictionary representation of the adapter, including its type."""
        base = super().dict_repr()
        base['type'] = 'agno'
        return base