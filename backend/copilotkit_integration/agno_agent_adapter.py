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
from typing import Any, AsyncGenerator, Dict, List, Optional, cast, Set, Callable

from copilotkit.agent import Agent as CopilotKitAgentBase
from copilotkit.action import ActionDict
from copilotkit.types import Message as CopilotKitMessage, MetaEvent
from copilotkit.protocol import (
    RuntimeEventTypes, 
    RuntimeMetaEventName, 
    RuntimeProtocolEvent,
    TextMessageStart,
    TextMessageContent,
    TextMessageEnd,
    NodeStarted, 
    NodeFinished, 
    RunStarted, 
    RunFinished, 
    RunError,
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
        frontend_action_schemas: List[ActionDict] = [],
        markdown_agent_for_tool_call_response: AgnoAgentInternal = None,
        tool_call_frontend_action_name:str = "display_tool_call_details",
        eligible_events_for_timeline:List[AgnoRunEvent] = [
            AgnoRunEvent.reasoning_step,
            AgnoRunEvent.reasoning_completed,
            AgnoRunEvent.tool_call_completed,
            AgnoRunEvent.run_error,
        ],
        tools_not_to_be_logged:List[str] = ["emit_frontend_action", "display_tool_call_details"],
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
                Defaults to None. The dedault setting expects below copilotkit action hook to show tool calls, if not presents, events will be ignored.
                
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
        self.tool_call_frontend_action_name = tool_call_frontend_action_name
        self.frontend_action_schemas = frontend_action_schemas
        self.eligible_events_for_timeline = eligible_events_for_timeline
        self.tools_not_to_be_logged = tools_not_to_be_logged

        self.is_markdown_agent_setup_done = False
        self.is_special_tool_for_frontend_action_setup = False   # Determines if the tool call logging setup has been done or not
        
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
        Manages TextMessageStart/End and NodeStarted/NodeFinished events correctly.
        """
        thread_id = execution_details["thread_id"]
        run_id = execution_details["run_id"]
        user_id = execution_details["user_id"]
        copilotkit_messages = cast(List[CopilotKitMessage], execution_details["messages"])
        agent_name = execution_details["agent_name"]

        run_has_finished = False
        current_text_message_id: Optional[str] = None

        try:
            # --- Preparation ---
            agno_messages = copilotkit_messages_to_agno(copilotkit_messages)
            last_user_message = agno_messages[-1] if agno_messages and agno_messages[-1].role == "user" else None
            if last_user_message is None:
                raise ValueError("No user message found.")

            # Initialize agent session (handles session_id, user_id, storage loading)
            # self.agno_agent.session_id = thread_id
            # self.agno_agent.user_id = user_id
            # if self.agno_agent.storage:
            #     self.agno_agent.load_session()

            # --- Initial State Update and Run Start ---
            self.update_copilot_agno_state() # Update state BEFORE RunStarted
            initial_state = self.copilot_agno_state.model_dump(exclude_none=True)
            await queue_put(RunStarted(type=RuntimeEventTypes.RUN_STARTED, state=initial_state), priority=True)
            await queue_put(NodeStarted(type=RuntimeEventTypes.NODE_STARTED, node_name="agno_run", state=initial_state), priority=True)

            # --- Execute Agno Stream ---
            if not hasattr(self.agno_agent, 'arun'):
                raise NotImplementedError("Agno agent instance must have an 'arun' method.")

            # Run the Agno agent and collect stream from it.
            agno_stream = await self.agno_agent.arun(
                message=last_user_message, stream=True, stream_intermediate_steps=True, messages=agno_messages
            )

            in_process_tool_call_ids: Set[str] = set()
            processed_tool_call_ids: Set[str] = set()
            event_summary = ""
            event_details = ""
            node_name_map = {
                AgnoRunEvent.reasoning_started.value: "reasoning",
                AgnoRunEvent.reasoning_completed.value: "reasoning",
                AgnoRunEvent.tool_call_started.value: "tool_call",
                AgnoRunEvent.tool_call_completed.value: "tool_call",
                AgnoRunEvent.updating_memory.value: "memory_update",
                AgnoRunEvent.run_error.value: "error_state" # Map error to a node state
            }
            frontend_action_names = execution_details.get("actions", [])

            # Process each chunk in the stream
            async for agno_chunk in agno_stream:
                agno_event_type = agno_chunk.event
                if not isinstance(agno_chunk, AgnoRunResponse):
                    continue # Skip non-response chunks if any

                # Check for cancellation signal from CopilotKit
                if execution_details.get("should_exit", False):
                    # TODO: Simulate Ctrl+C for Agno to stop current run
                    raise RunCancelledException("Run cancelled by CopilotKit.")

                events_to_queue: List[RuntimeProtocolEvent] = []

                # --- Process Text Message Events from Agno ---
                if agno_chunk.content and agno_event_type == AgnoRunEvent.run_response.value:
                    if current_text_message_id is None:
                        # Start a new text message block if one isn't active
                        current_text_message_id = str(uuid.uuid4())
                        start_event = TextMessageStart(type=RuntimeEventTypes.TEXT_MESSAGE_START, messageId=current_text_message_id, parentMessageId=None) # type: ignore
                        events_to_queue.append(start_event)
                    events_to_queue.append(TextMessageContent(type=RuntimeEventTypes.TEXT_MESSAGE_CONTENT, messageId=current_text_message_id, content=str(agno_chunk.content))) # type: ignore
                else:
                    # Close the current text message block if there was one active and we encountered a non-text content chunk
                    if current_text_message_id is not None:
                        end_event = TextMessageEnd(type=RuntimeEventTypes.TEXT_MESSAGE_END, messageId=current_text_message_id) # type: ignore
                        events_to_queue.append(end_event)
                        current_text_message_id = None # Reset message ID

                # --- Process Tool Call Events from Agno ---
                #  For each tool call event, we need to emit ActionExecutionStart, ActionExecutionArgs, ActionExecutionEnd, ActionExecutionResult
                #  and also update the state object with the tool call details in timeline
                if agno_event_type in [
                    AgnoRunEvent.tool_call_started.value,
                    AgnoRunEvent.tool_call_completed.value,
                ]:
                    for tool_call in agno_chunk.tools:
                        tool_call_id = tool_call.get("tool_call_id", str(uuid.uuid4()))
                        actual_tool_name = tool_call.get("tool_name", "unknown_tool")
                        tool_name = actual_tool_name.replace(" ", "_").title()
                        tool_args_dict = tool_call.get("tool_args", {})
                        tool_result = tool_call.get("content") # Result is in 'content' for completed tool calls

                        if agno_event_type == AgnoRunEvent.tool_call_started.value:
                            if tool_call_id in in_process_tool_call_ids:
                                continue # Skip if we've already processed this tool call
                            in_process_tool_call_ids.add(tool_call_id)
                            
                            event_summary = f"Calling tool: {tool_name}"
                            event_details = str(tool_result) if tool_result is not None else None

                        if agno_event_type == AgnoRunEvent.tool_call_completed.value:
                            if tool_call_id in processed_tool_call_ids:
                                continue # Skip if we've already processed this tool call
                            processed_tool_call_ids.add(tool_call_id)
                            
                            # --- Process Tool Call Result, If Markdown Agent Given ---
                            if self.markdown_agent_for_tool_call_response and tool_result:
                                # Process the tool result using the markdown agent
                                self._setup_markdown_agent()
                                markdown_result = await self.markdown_agent_for_tool_call_response.arun(
                                    message=tool_result, stream=True, stream_intermediate_steps=True, messages=[]
                                )
                                tool_result = ""
                                async for markdown_chunk in markdown_result:
                                    tool_result += str(markdown_chunk.content)
                                tool_result = tool_result.strip()
                            
                            event_summary = f"Finished calling tool: {tool_name}"
                            event_details = str(tool_result) if tool_result else ""

                        # --- Emit Action Events ---
                        # For frontend action, special tool `emit_frontend_action()` registered to Agno will take care of emitting the Action message events
                        # For backend action (agno tool call - for both start and end tool call events), send the ActionExecutionStart, ActionExecutionArgs, ActionExecutionEnd, ActionExecutionResult to log the tool call
                        if tool_name != "emit_frontend_action": # we dont want to log the special tool itself as its internal tool
                            if self.tool_call_frontend_action_name and actual_tool_name not in self.tools_not_to_be_logged:
                                print(f">>>>>>> Emitting ActionExecution** Events for tool: {tool_name}")
                                # It's a regular backend tool registered to Agno, so simply emit Action events in case frontend wants to show the tool call details
                                # Frontend must setup an action with name as self.tool_call_frontend_action_name (default: `display_tool_call_details`)
                                events_to_queue.append({"type": RuntimeEventTypes.ACTION_EXECUTION_START, "actionExecutionId": tool_call_id, "actionName": self.tool_call_frontend_action_name, "parentMessageId": None})
                                events_to_queue.append({"type": RuntimeEventTypes.ACTION_EXECUTION_ARGS, "actionExecutionId": tool_call_id, "args": json.dumps({"tool_call_summary": f"Called {tool_name}", "tool_name": tool_name, "tool_args": json.dumps(tool_args_dict), "tool_result": tool_result})}) # Ensure args are string
                                events_to_queue.append({"type": RuntimeEventTypes.ACTION_EXECUTION_END, "actionExecutionId": tool_call_id})
                                events_to_queue.append({"type": RuntimeEventTypes.ACTION_EXECUTION_RESULT, "actionExecutionId": tool_call_id, "actionName": self.tool_call_frontend_action_name, "result": json.dumps({'BackendResult': 'Execution done'})})

                        # --- Update State Object and Emit Node Lifecycle Events ---
                        # Update state and timeline *if* this event is eligible or triggers lifecycle emission
                        self.update_copilot_agno_state() # Refresh base state
                        if event_summary and (agno_event_type in self.eligible_events_for_timeline):
                            # Append the new event to the timeline
                            #  Dont add the special tools itself as those are internal tool
                            if agno_event_type in self.eligible_events_for_timeline and actual_tool_name not in self.tools_not_to_be_logged:
                                self.copilot_agno_state.event_timeline.append(TimelineEvent(
                                    event_type=str(agno_event_type), # Store enum value as string
                                    event_summary=event_summary,
                                    event_details=event_details
                                ))
                        current_state = self.copilot_agno_state.model_dump(exclude_none=True) # Get state WITH timeline

                        # Emit corresponding CopilotKit lifecycle events - this will internally emit AgentStateMessages
                        if agno_event_type in node_name_map:
                            node_name = node_name_map.get(agno_event_type)

                            if node_name:
                                if agno_event_type == AgnoRunEvent.tool_call_started.value:
                                    events_to_queue.append(NodeStarted(type=RuntimeEventTypes.NODE_STARTED, node_name=node_name, state=current_state))
                                if agno_event_type == AgnoRunEvent.tool_call_completed.value:
                                    events_to_queue.append(NodeFinished(type=RuntimeEventTypes.NODE_FINISHED, node_name=node_name, state=current_state))
                    
                    # --- Emit Collected Events ---
                    if events_to_queue:
                        await queue_put(*events_to_queue)
                    
                    # Skip further processing for tool call events and move to next chunk
                    # This is done as entries in tool calls array are repeated for each tool call so we need to filter the earlier processed tool calls; for other events thats not the case.
                    continue
                
                # --- Process Reasoning Events from Agno ---
                elif agno_event_type == AgnoRunEvent.reasoning_started.value:
                    event_summary = "Agent started reasoning."
                elif agno_event_type == AgnoRunEvent.reasoning_completed.value:
                    reasoning_details = getattr(agno_chunk, 'thinking', getattr(agno_chunk, 'content', None))
                    event_summary = "Agent finished reasoning."
                    event_details = str(reasoning_details) if reasoning_details else ""
                elif agno_event_type == AgnoRunEvent.reasoning_step.value: # TODO: Cross check if this logic is fine or not
                    # Capture reasoning step details for the timeline
                    step_content = getattr(agno_chunk, 'content', None)
                    if step_content and hasattr(step_content, 'title'):
                        event_summary = f"Reasoning Step: {step_content.title or 'Untitled'}"
                        event_details = str(step_content) # Store full step details
                    else:
                        event_summary = "Processing reasoning step."
                elif agno_event_type == AgnoRunEvent.updating_memory.value:
                    event_summary = "Agent is updating its memory."
                elif agno_event_type == AgnoRunEvent.run_error.value:
                    error_content = str(getattr(agno_chunk, 'content', 'Unknown error'))
                    event_summary = "Agent encountered an error."
                    event_details = f"Error Details: {error_content}"

                self.update_copilot_agno_state() # Refresh base state
                if agno_event_type in self.eligible_events_for_timeline:
                    # Append the new event to the timeline
                    self.copilot_agno_state.event_timeline.append(TimelineEvent(
                        event_type=str(agno_event_type), # Store enum value as string
                        event_summary=event_summary,
                        event_details=event_details
                    ))
                current_state = self.copilot_agno_state.model_dump(exclude_none=True) # Get state WITH timeline

                # Emit corresponding CopilotKit lifecycle events - this will internally emit AgentStateMessages
                if agno_event_type in node_name_map:
                    node_name = node_name_map.get(agno_event_type)

                    if node_name:
                        if agno_event_type.endswith("Started") or agno_event_type == AgnoRunEvent.updating_memory.value:
                            events_to_queue.append(NodeStarted(type=RuntimeEventTypes.NODE_STARTED, node_name=node_name, state=current_state))
                        if agno_event_type.endswith("Completed") or agno_event_type in [AgnoRunEvent.updating_memory.value, AgnoRunEvent.run_error.value]:
                            events_to_queue.append(NodeFinished(type=RuntimeEventTypes.NODE_FINISHED, node_name=node_name, state=current_state))

                # --- Emit Collected Events ---
                if events_to_queue:
                    await queue_put(*events_to_queue)

                # If Agno runs into an issue, emit RunError and RunFinished signals
                if agno_event_type == AgnoRunEvent.run_error.value:
                    await queue_put(RunError(type=RuntimeEventTypes.RUN_ERROR, error=str(agno_chunk.content)), priority=True)
                    await queue_put(RunFinished(type=RuntimeEventTypes.RUN_FINISHED, state=current_state), priority=True)
                    run_has_finished = True
                    break # Stop processing stream on error

            # --- After Processing All Chunks ---
            if not run_has_finished:
                # Ensure any final open text message is closed
                if current_text_message_id is not None:
                    await queue_put(TextMessageEnd(type=RuntimeEventTypes.TEXT_MESSAGE_END, messageId=current_text_message_id))

                self.update_copilot_agno_state() # Final state update
                final_state = self.copilot_agno_state.model_dump(exclude_none=True)
                await queue_put(NodeFinished(type=RuntimeEventTypes.NODE_FINISHED, node_name="agno_run", state=final_state))
                await queue_put(RunFinished(type=RuntimeEventTypes.RUN_FINISHED, state=final_state), priority=True)
                run_has_finished = True

        # --- Exception Handling ---
        except RunCancelledException:
            log_info(f"[Copilotkit-Agno][{agent_name}] Run {run_id} cancelled.")
            if current_text_message_id is not None: await queue_put(TextMessageEnd(type=RuntimeEventTypes.TEXT_MESSAGE_END, messageId=current_text_message_id))
            self.update_copilot_agno_state() # Update state before final RunFinished
            final_state = self.copilot_agno_state.model_dump(exclude_none=True)
            await queue_put(NodeFinished(type=RuntimeEventTypes.NODE_FINISHED, node_name="agno_run", state=final_state))
            await queue_put(RunFinished(type=RuntimeEventTypes.RUN_FINISHED, state=final_state), priority=True)
            run_has_finished = True
        except Exception as e:
            log_exception(f"[Copilotkit-Agno][{agent_name}] Error during Agno agent streaming: {e}")
            traceback.print_exc()
            if current_text_message_id is not None: await queue_put(TextMessageEnd(type=RuntimeEventTypes.TEXT_MESSAGE_END, messageId=current_text_message_id))
            self.update_copilot_agno_state() # Update state before RunError/RunFinished
            final_state = self.copilot_agno_state.model_dump(exclude_none=True)
            await queue_put(RunError(type=RuntimeEventTypes.RUN_ERROR, error=str(e)), priority=True)
            await queue_put(RunFinished(type=RuntimeEventTypes.RUN_FINISHED, state=final_state), priority=True)
            run_has_finished = True

        # --- Finalization ---
        finally:
            if not run_has_finished:
                 log_exception(f"[Copilotkit-Agno][{agent_name}] Agno stream ended unexpectedly for run {run_id}. Signalling RunFinished.")
                 if current_text_message_id is not None: await queue_put(TextMessageEnd(type=RuntimeEventTypes.TEXT_MESSAGE_END, messageId=current_text_message_id))
                 self.update_copilot_agno_state()
                 final_state = self.copilot_agno_state.model_dump(exclude_none=True)
                 await queue_put(RunFinished(type=RuntimeEventTypes.RUN_FINISHED, state=final_state), priority=True)

            # Persist final state if storage is configured
            if self.agno_agent.storage:
                 try:
                    # Ensure thread_id and user_id are set before saving
                    current_thread_id = self.agno_agent.session_id or thread_id
                    current_user_id = self.agno_agent.user_id or user_id
                    self.agno_agent.write_to_storage(session_id=current_thread_id, user_id=current_user_id)
                    log_debug(f"[Copilotkit-Agno][{agent_name}] Final state saved for thread {current_thread_id}.")
                 except Exception as save_err:
                    log_exception(f"[Copilotkit-Agno][{agent_name}] Error saving final state for thread {thread_id}: {save_err}")
            log_debug(f"[Copilotkit-Agno][{agent_name}] Agno agent processing coroutine finished for run {run_id}")


    def _get_special_tool_for_frontend_actions(self, actions: List[Dict[str, Any]]) -> Callable:
        """
        Returns a special tool for Agno that can be used to invoke frontend actions.
        This tool ONLY emits Start and Args events to trigger the frontend handler.
        """
        action_names = [action['name'] for action in actions]
        async def emit_frontend_action(action_name: str, action_args: Dict[str, Any]) -> str:
            f"""
            Use this tool to trigger frontend actions to perform UI-related tasks.
            Available frontend actions:
                {actions}

            Note:
                - The action_name must be one of the available actions listed above.
                - The action_args must be a dictionary matching the parameters of the specified action_name.
                - Only call available actions with correct arguments.

            Args:
                action_name (str): The name of the frontend action to trigger.
                action_args (Dict[str, Any]): The arguments to pass to the frontend action.

            Returns:
                str: A status message indicating the frontend action has been requested.
                     The actual execution happens on the frontend.
            """
            if action_name in action_names and action_name != self.tool_call_frontend_action_name:
                events_to_queue = []
                # Generate a unique ID for this specific invocation attempt
                frontend_action_execution_id = str(uuid.uuid4())
                log_info(f"[Copilotkit-Agno][{self.name}] Requesting frontend action '{action_name}' with execution ID: {frontend_action_execution_id} and args: {action_args}")

                events_to_queue.append({"type": RuntimeEventTypes.ACTION_EXECUTION_START, "actionExecutionId": frontend_action_execution_id, "actionName": action_name, "parentMessageId": None})
                events_to_queue.append({"type": RuntimeEventTypes.ACTION_EXECUTION_ARGS, "actionExecutionId": frontend_action_execution_id, "args": json.dumps(action_args)})
                events_to_queue.append({"type": RuntimeEventTypes.ACTION_EXECUTION_END, "actionExecutionId": frontend_action_execution_id})
                events_to_queue.append({"type": RuntimeEventTypes.ACTION_EXECUTION_RESULT, "actionExecutionId": frontend_action_execution_id, "actionName": action_name, "result": json.dumps({'BackendResult': 'Execution done'})})

                try:
                    await queue_put(*events_to_queue)
                    # Return a confirmation that the *request* was sent
                    return f"Request sent to frontend to execute action '{action_name}' with args: {action_args}"
                except Exception as qe:
                    log_error(f"[Copilotkit-Agno][{self.name}] Error queueing frontend action events: {qe}")
                    return f"Error: Could not queue request for frontend action '{action_name}'."

            else:
                log_warning(f"[Copilotkit-Agno][{self.name}] Agent tried to invoke unknown frontend action with name: '{action_name}', args: {action_args}")
                # Provide clearer feedback to the LLM
                available_action_details = ""
                for action in actions:
                    available_action_details += f"Action Name: {action['name']}\n"
                    available_action_details += f"Description: {action.get('description', 'No description provided')}\n"
                    available_action_details += f"Parameters: {json.dumps(action.get('parameters', {}), indent=2)}\n"
                    available_action_details += "---\n"
                    
                return dedent(f"""
                Error: Action '{action_name}' not found.
                Available frontend actions are:
                 {available_action_details}.
                Please choose one of the available actions and provide the correct arguments.
                """)
        return emit_frontend_action

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
            actions=(actions or []) + self.frontend_action_schemas,
            user_id=user_id, # Pass the determined user ID
        )

        all_known_actions = execution_details.get('actions', [])

        # Register a special tool for frontend actions
        if all_known_actions:
            available_action_details = ""
            for action in all_known_actions:
                available_action_details += f"Action Name: {action['name']}\n"
                available_action_details += f"Description: {action.get('description', 'No description provided')}\n"
                available_action_details += f"Parameters: {json.dumps(action.get('parameters', {}), indent=2)}\n"
                available_action_details += "---\n"
            if not self.is_special_tool_for_frontend_action_setup:
                self.agno_agent.instructions = self.agno_agent.instructions or []
                self.agno_agent.instructions.append("You also have an ability to call frontend actions to perform UI-related tasks.")
                self.agno_agent.instructions.append("   Use the `emit_frontend_action` tool to request frontend actions.")
                self.agno_agent.instructions.append("   The `emit_frontend_action` tool takes two arguments: action_name and action_args.")
                self.agno_agent.instructions.append("   The action_name should be the name of the frontend action you want to call.")
                self.agno_agent.instructions.append("   The action_args should be a dictionary of arguments for the frontend action.")
                self.agno_agent.instructions.append("   The frontend will then execute the action and may return a result. You may use it for human-in-the-loop interaction (user approval, additional user input kind of interactions).")
                self.agno_agent.instructions.append("   DO ENSURE, that the action_name you pass in `emit_frontend_action`, should be one of the below given action names")
                self.agno_agent.instructions.append("   DO ENSURE, the passed action_args should match the parameters of the specified action_name.")
                self.agno_agent.instructions.append("   If the required action is not available, DO NOT CALL THE `emit_frontend_action` TOOL AND RESPOND APPROPRIATELY TO THE USER.")
                self.agno_agent.instructions.append(dedent(f"""
                Below is the list of frontend actions:
                    {available_action_details}
                """))
                emit_frontend_action = self._get_special_tool_for_frontend_actions(all_known_actions)
                # Register this special tool with Agno agent instance
                self.agno_agent.tools.append(emit_frontend_action)
                self.is_special_tool_for_frontend_action_setup = True

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