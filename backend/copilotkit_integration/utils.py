# copilotkit_integration/utils.py
import json
import uuid
from typing import Any, Dict, List, Optional, Sequence, cast
from pydantic import BaseModel, Field

from copilotkit.types import Message as CopilotKitMessage
from copilotkit.protocol import (
    RuntimeProtocolEvent, # Type alias for event dictionaries
    RuntimeEventTypes,    # Enum for event types
    TextMessageStart,
    TextMessageContent,
    TextMessageEnd,
    ActionExecutionStart,
    ActionExecutionArgs,
    ActionExecutionEnd,
    ActionExecutionResult,
)

# Import Agno classes
from agno.models.message import Message as AgnoMessage
from agno.run.response import (
    RunResponse as AgnoRunResponse, 
    RunEvent as AgnoRunEvent
)

class TimelineEvent(BaseModel):
    """Represents a single event in the lifecycle timeline."""
    event_type: str
    event_summary: str
    event_details: str

class CopilotKitStateProperties(BaseModel):
    """CopilotKit properties"""
    actions: List[Any] = Field(default_factory=list)

class CopilotKitAgnoState(BaseModel):
    """CopilotKit state"""
    messages: List[Any] = Field(default_factory=list)
    copilotkit: CopilotKitStateProperties = Field(default_factory=CopilotKitStateProperties)
    event_timeline: List[TimelineEvent] = Field(default_factory=list) # Timeline of lifecycle events
    session_state: Dict = Field(default_factory=dict)


# --- Message Conversion ---
# (copilotkit_messages_to_agno and agno_messages_to_copilotkit remain the same as corrected before)
# ...
def copilotkit_messages_to_agno(copilotkit_messages: List[CopilotKitMessage]) -> List[AgnoMessage]:
    """Converts CopilotKit messages to Agno messages."""
    agno_messages = []
    for msg in copilotkit_messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        msg_id = msg.get("id", str(uuid.uuid4()))
        tool_calls = None
        tool_call_id = None
        msg_type = msg.get("type")
        if msg_type == "ActionExecutionMessage":
            role = "assistant" # Tool calls originate from assistant
            tool_calls = [{
                "id": msg_id, # Use message ID as tool call ID
                "type": "function",
                "function": {
                    "name": msg.get("name", "unknown_function"),
                    "arguments": json.dumps(msg.get("arguments", {})) # Ensure arguments are stringified JSON
                }
            }]
            content = None # Tool call messages might not have text content
        elif msg_type == "ResultMessage":
            role = "tool"
            tool_call_id = msg.get("actionExecutionId")
            content = msg.get("result") # Tool result content

        try:
            agno_msg = AgnoMessage(
                role=role,
                content=content,
                id=msg_id,
                tool_calls=tool_calls,
                tool_call_id=tool_call_id,
                name=msg.get("name") if msg_type != "ResultMessage" else msg.get("actionName")
            )
            agno_messages.append(agno_msg)
        except Exception as e:
             print(f"Warning: Failed to create AgnoMessage from CopilotKit message: {msg}. Error: {e}")

    return agno_messages

def agno_messages_to_copilotkit(agno_messages: List[AgnoMessage]) -> List[CopilotKitMessage]:
    """Converts Agno messages back to CopilotKit message format (for get_state)."""
    copilotkit_messages = []
    for msg in agno_messages:
         copilotkit_msg: Dict[str, Any] = {
             "id": getattr(msg, 'id', str(uuid.uuid4())),
             "role": msg.role,
             "content": msg.get_content_string(),
             "createdAt": getattr(msg, 'created_at', None)
         }

         if msg.role == "tool" and msg.tool_call_id:
             copilotkit_msg["type"] = "ResultMessage"
             copilotkit_msg["actionExecutionId"] = msg.tool_call_id
             copilotkit_msg["actionName"] = msg.tool_name or "unknown_tool"
             copilotkit_msg["result"] = copilotkit_msg.pop("content") # Use 'result' field
             copilotkit_msg.pop("role")
         elif msg.role == "assistant" and msg.tool_calls:
             pass # Skip tool call request messages for now in get_state
         elif msg.role in ["user", "assistant", "system"]:
              copilotkit_msg["type"] = "TextMessage"
              if "content" in copilotkit_msg and not isinstance(copilotkit_msg["content"], str):
                  copilotkit_msg["content"] = str(copilotkit_msg["content"])
         else:
              print(f"Warning: Skipping Agno message with unmappable role: {msg.role}")
              continue

         copilotkit_messages.append(cast(CopilotKitMessage, copilotkit_msg))
    return copilotkit_messages

# --- Event Mapping ---

def map_agno_chunk_to_copilotkit_protocol_events(
    chunk: AgnoRunResponse,
) -> List[RuntimeProtocolEvent]:
    """
    Maps an AgnoRunResponse chunk to CopilotKit PROTOCOL events (TextMessageContent, ActionExecution*).
    It translates the *content* of this specific chunk. It does NOT handle TextMessageStart/End.
    """
    events: List[RuntimeProtocolEvent] = []
    event_type = chunk.event

    # 1. Handle Text Content (only if it's the main event type)
    if chunk.content and event_type == AgnoRunEvent.run_response.value:
        # Pass a placeholder ID, the caller will manage the actual message ID state
        events.append(TextMessageContent(type=RuntimeEventTypes.TEXT_MESSAGE_CONTENT, messageId="PLACEHOLDER_ID", content=str(chunk.content)))

    # 2. Handle Tool Call Results (Assuming tools appear when the call is *complete*)
    is_tool_completion_event = event_type == AgnoRunEvent.tool_call_completed.value
    is_likely_tool_result_chunk = chunk.tools and not (chunk.content and event_type == AgnoRunEvent.run_response.value)

    if chunk.tools and (is_tool_completion_event or is_likely_tool_result_chunk):
        for tool_call in chunk.tools:
            tool_call_id = tool_call.get("tool_call_id", str(uuid.uuid4()))
            tool_name = tool_call.get("tool_name", "unknown_tool")
            tool_args = tool_call.get("tool_args", {})
            tool_result = tool_call.get("content")

            # Emit the *full sequence* for a completed tool call result here.
            # The caller loop will use processed_tool_call_ids to avoid duplication.
            events.append(ActionExecutionStart(type=RuntimeEventTypes.ACTION_EXECUTION_START, actionExecutionId=tool_call_id, actionName=tool_name, parentMessageId=None))
            events.append(ActionExecutionArgs(type=RuntimeEventTypes.ACTION_EXECUTION_ARGS, actionExecutionId=tool_call_id, args=json.dumps(tool_args)))
            events.append(ActionExecutionEnd(type=RuntimeEventTypes.ACTION_EXECUTION_END, actionExecutionId=tool_call_id))
            if tool_result is not None:
                events.append(ActionExecutionResult(type=RuntimeEventTypes.ACTION_EXECUTION_RESULT, actionExecutionId=tool_call_id, actionName=tool_name, result=str(tool_result)))

    return events # type: ignore

# --- State Filtering ---

def filter_agno_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """Filters sensitive or unnecessary state before sending to frontend."""
    filtered = {k: v for k, v in state.items() if not k.startswith("_")}
    return filtered