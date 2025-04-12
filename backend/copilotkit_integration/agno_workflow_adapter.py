# copilotkit_integration/agno_workflow_adapter.py
import asyncio
import json
import traceback
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional, cast

from copilotkit.agent import Agent as CopilotKitAgentBase
from copilotkit.action import ActionDict
from copilotkit.types import Message as CopilotKitMessage, MetaEvent
from copilotkit.protocol import RuntimeMetaEventName, RuntimeEventTypes, RunStarted, NodeStarted, NodeFinished, RunFinished, RunError
# Import LIFECYCLE events
from copilotkit.runloop import (
    copilotkit_run, CopilotKitRunExecution, get_context_execution, queue_put
)

# Import Agno classes
from agno.workflow.workflow import Workflow as AgnoWorkflowInternal
from agno.run.response import RunResponse as AgnoRunResponse, RunEvent as AgnoRunEvent
from agno.exceptions import RunCancelledException
from agno.memory.workflow import WorkflowMemory
# from agno.memory.v2 import Memory as MemoryV2 # If workflow uses V2 memory
from agno.storage.session.workflow import WorkflowSession # Import specific session type


from .utils import (
    map_agno_chunk_to_copilotkit_protocol_events,
    filter_agno_state,
    # Message conversion likely less relevant here
)

class AgnoWorkflowAdapter(CopilotKitAgentBase):
    """CopilotKit Adapter for running Agno Workflows with streaming using runloop."""
    def __init__(
        self,
        agno_workflow_instance: AgnoWorkflowInternal,
        user_id_property: str = "userId",
        input_property: str = "input",
        **kwargs,
    ):
        super().__init__(
            name=agno_workflow_instance.name or agno_workflow_instance.__class__.__name__,
            description=agno_workflow_instance.description,
            **kwargs,
        )
        self.agno_workflow = agno_workflow_instance
        self.user_id_property = user_id_property
        self.input_property = input_property
        if self.agno_workflow.storage:
            self.agno_workflow.storage.mode = "workflow"

    async def _process_agno_stream_and_queue(self, execution_details: CopilotKitRunExecution) -> None:
        """
        Coroutine that executes Agno workflow's run, maps events, and puts them on the queue.
        """
        thread_id = execution_details["thread_id"]
        run_id = execution_details["run_id"]
        user_id = execution_details["user_id"]
        workflow_input = execution_details.get(self.input_property, {})

        # Configure Agno workflow instance
        self.agno_workflow.session_id = thread_id
        self.agno_workflow.user_id = user_id
        if self.agno_workflow.storage:
            self.agno_workflow.read_from_storage() # Load state

        current_agno_state = self.agno_workflow.session_state or {}

        # Put RunStarted lifecycle event
        await queue_put(RunStarted(type=RuntimeEventTypes.RUN_STARTED, state=current_agno_state), priority=True)
        # Put initial NodeStarted
        await queue_put(NodeStarted(type=RuntimeEventTypes.NODE_STARTED, node_name="agno_workflow_run", state=current_agno_state), priority=True)


        # Execute Agno workflow stream
        try:
            if not hasattr(self.agno_workflow, 'run'):
                raise NotImplementedError(f"Agno workflow {self.agno_workflow.name} does not have a run method.")

            # Assuming run returns async iterator when stream=True
            agno_stream = self.agno_workflow.run(stream=True, stream_intermediate_steps=True, **workflow_input)

            # Check if it's actually an async iterator
            if isinstance(agno_stream, AsyncGenerator):
                 async for agno_chunk in agno_stream:
                      if not isinstance(agno_chunk, AgnoRunResponse):
                           print(f"Warning: Received unexpected chunk type from Agno Workflow: {type(agno_chunk)}")
                           continue

                      if get_context_execution().get("should_exit", False):
                           print(f"Cancellation requested for workflow run {run_id}")
                           raise RunCancelledException("Workflow execution cancelled by frontend.")

                      current_agno_state = self.agno_workflow.session_state or {}
                      protocol_events = map_agno_chunk_to_copilotkit_protocol_events(agno_chunk)
                      if protocol_events:
                          await queue_put(*protocol_events)

                      # Map lifecycle events (similar to Agent adapter)
                      agno_event_type = agno_chunk.event
                      if agno_event_type == AgnoRunEvent.run_error.value:
                          await queue_put(RunError(type=RuntimeEventTypes.RUN_ERROR, error=str(agno_chunk.content)))
                          break
                      # Add mappings for other relevant AgnoRunEvents to NodeStarted/NodeFinished if needed
            else:
                 # Handle case where run is synchronous but returns an iterator
                 # This might require asyncio.to_thread for the iteration itself
                 print("Warning: Agno workflow run method did not return an async generator. Streaming might block.")
                 for agno_chunk in agno_stream: # Assuming sync iterator
                     if not isinstance(agno_chunk, AgnoRunResponse): continue
                     if get_context_execution().get("should_exit", False): raise RunCancelledException("Cancelled.")
                     current_agno_state = self.agno_workflow.session_state or {}
                     protocol_events = map_agno_chunk_to_copilotkit_protocol_events(agno_chunk)
                     if protocol_events: await queue_put(*protocol_events)
                     # map lifecycle events...


            # Put final NodeFinished and RunFinished events
            await queue_put(NodeFinished(type=RuntimeEventTypes.NODE_FINISHED, node_name="agno_workflow_run", state=current_agno_state))
            await queue_put(RunFinished(type=RuntimeEventTypes.RUN_FINISHED, state=current_agno_state), priority=True)

        except RunCancelledException:
             print(f"Workflow Run {run_id} cancelled.")
             await queue_put(RunFinished(type=RuntimeEventTypes.RUN_FINISHED, state=current_agno_state), priority=True)
        except Exception as e:
            print(f"Error during Agno workflow streaming: {e}")
            traceback.print_exc()
            await queue_put(RunError(type=RuntimeEventTypes.RUN_ERROR, error=e))
        finally:
            if self.agno_workflow.storage:
                self.agno_workflow.write_to_storage()
            print(f"Agno workflow processing finished for run {run_id}")

    def execute(
        self,
        *,
        state: dict,
        messages: List[CopilotKitMessage],
        thread_id: str,
        actions: Optional[List[ActionDict]] = None,
        meta_events: Optional[List[MetaEvent]] = None,
        properties: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """Executes the Agno workflow using CopilotKit runloop."""
        run_id = str(uuid.uuid4())
        user_id = (properties or {}).get(self.user_id_property, "default_user")

        should_interrupt = False
        if meta_events:
            for event in meta_events:
                if event.get("name") == RuntimeMetaEventName.EXIT.value:
                    should_interrupt = True
                    break

        workflow_input = (properties or {}).get(self.input_property, kwargs.get(self.input_property, {}))

        execution_details = CopilotKitRunExecution(
            thread_id=thread_id,
            agent_name=self.name,
            run_id=run_id,
            should_exit=should_interrupt,
            node_name="start",
            is_finished=False,
            predict_state_configuration={},
            predicted_state={},
            argument_buffer="",
            current_tool_call=None,
            state=state,
            # Pass workflow-specific details
            workflow_input=workflow_input, # Pass structured input
            user_id=user_id,
            messages=messages, # Pass for context if needed
            actions=actions or [], # Pass for context if needed
        )

        return copilotkit_run(
            fn=lambda: self._process_agno_stream_and_queue(execution_details),
            execution=execution_details
        )

    # get_state and dict_repr remain the same as the corrected version previously provided
    async def get_state(self, *, thread_id: str) -> Dict[str, Any]:
        """Retrieves the persisted state of the Agno workflow session from storage."""
        workflow_name = self.agno_workflow.name or self.__class__.__name__
        print(f"[{workflow_name}] get_state called for thread_id: {thread_id}")

        if not self.agno_workflow.storage:
             print(f"[{workflow_name}] No storage configured for workflow.")
             return {"threadId": thread_id, "threadExists": False, "state": {}, "messages": []}

        try:
            agno_session: Optional[WorkflowSession] = self.agno_workflow.storage.read(session_id=thread_id) # type: ignore
            if agno_session is None:
                print(f"[{workflow_name}] Session not found in storage for thread_id: {thread_id}")
                return {"threadId": thread_id, "threadExists": False, "state": {}, "messages": []}

            print(f"[{workflow_name}] Session found for thread_id: {thread_id}")

            agno_state = agno_session.session_data.get("session_state", {}) if agno_session.session_data else {}
            filtered_state = filter_agno_state(agno_state)

            copilotkit_messages = []
            if agno_session.memory:
                # Check if the memory structure corresponds to Memory V2 or legacy WorkflowMemory
                # ******** Enable this `if` block and re-check once V2 Memory is available in Agno
                #  if "runs" in agno_session.memory and isinstance(self.agno_workflow.memory, MemoryV2):
                #       # V2 Memory might store runs differently, adapt if needed
                #       print(f"[{workflow_name}] Workflow using MemoryV2, message extraction from runs not implemented yet.")
                #       pass
                 # Logic for legacy WorkflowMemory
                 if "runs" in agno_session.memory and isinstance(agno_session.memory["runs"], list):
                      # Convert workflow runs to messages if desired (conceptual)
                      # agno_messages = []
                      # for run in agno_session.memory["runs"]:
                      #     if run.get("input"): agno_messages.append(AgnoMessage(role="user", content=str(run["input"])))
                      #     if run.get("response") and run["response"].get("content"): agno_messages.append(AgnoMessage(role="assistant", content=str(run["response"]["content"])))
                      # copilotkit_messages = agno_messages_to_copilotkit(agno_messages)
                      pass # Keep empty for now

            print(f"[{workflow_name}] Returning state for thread_id: {thread_id} with {len(copilotkit_messages)} messages.")
            return {"threadId": thread_id, "threadExists": True, "state": filtered_state, "messages": copilotkit_messages }
        except Exception as e:
             print(f"Error getting Agno workflow state for thread {thread_id}: {e}")
             traceback.print_exc()
             return {"threadId": thread_id, "threadExists": False, "state": {"error": f"Failed to retrieve state: {str(e)}"}, "messages": []}


    def dict_repr(self) -> Dict[str, Any]:
        base = super().dict_repr()
        base['type'] = 'agno_workflow'
        # Only add parameters if they exist and are not None
        if hasattr(self.agno_workflow, '_run_parameters') and self.agno_workflow._run_parameters:
            base['parameters'] = self.agno_workflow._run_parameters
        return base