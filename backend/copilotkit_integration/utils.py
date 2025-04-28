# copilotkit_integration/utils.py
import json
import uuid
import time # Import time module
from datetime import datetime, timezone, timedelta # Import datetime stuff
from typing import Any, Dict, List, Optional, Sequence, cast, Set
from pydantic import BaseModel, Field, ConfigDict

# Import specific event types for clarity
from copilotkit.types import Message as CopilotKitMessage
from copilotkit.protocol import (
    RuntimeProtocolEvent,
    RuntimeEventTypes,
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

# --- Pydantic Models for State Management ---
# (Keep TimelineEvent, CopilotKitStateProperties, CopilotKitAgnoState as they were)
class TimelineEvent(BaseModel):
    """Represents a single event in the lifecycle timeline."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    timestamp: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    event_summary: str
    event_details: str = ""

class CopilotKitStateProperties(BaseModel):
    """CopilotKit properties expected by the frontend."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    actions: List[Any] = Field(default_factory=list)

class CopilotKitAgnoState(BaseModel):
    """Internal state representation matching frontend expectations."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    messages: List[Any] = Field(default_factory=list)
    copilotkit: CopilotKitStateProperties = Field(default_factory=CopilotKitStateProperties)
    event_timeline: List[TimelineEvent] = Field(default_factory=list)
    session_state: Dict[str, Any] = Field(default_factory=dict)


# --- Message Conversion ---

def copilotkit_messages_to_agno(copilotkit_messages: List[CopilotKitMessage]) -> List[AgnoMessage]:
    """Converts CopilotKit messages to Agno messages, handling timestamp conversion."""
    agno_messages = []
    tool_calls_by_parent_id: Dict[str, List[Dict[str, Any]]] = {}
    created_assistant_messages: Set[str] = set()

    # First pass: Group tool calls by potential parent message ID
    for msg in copilotkit_messages:
        msg_type = msg.get("type")
        msg_id = msg.get("id", str(uuid.uuid4()))
        if msg_type == "ActionExecutionMessage":
            parent_id = msg.get("parentMessageId") or msg_id # Assume msg_id is parent if not specified
            if parent_id not in tool_calls_by_parent_id:
                tool_calls_by_parent_id[parent_id] = []
            tool_calls_by_parent_id[parent_id].append({
                "id": msg_id,
                "type": "function",
                "function": {
                    "name": msg.get("name", "unknown_function"),
                    "arguments": json.dumps(msg.get("arguments", {})),
                },
            })

    # Second pass: Create Agno messages
    for msg in copilotkit_messages:
        role = msg.get("role", "user")
        content = msg.get("content", None)
        msg_id = msg.get("id", str(uuid.uuid4()))
        tool_calls = None
        tool_call_id = None
        tool_name = None
        msg_type = msg.get("type")
        created_at_ts: Optional[int] = None

        # Convert timestamp
        created_at_str = msg.get("createdAt")
        if created_at_str and isinstance(created_at_str, str):
            try:
                # Handle Z timezone designator
                if created_at_str.endswith("Z"):
                    created_at_str = created_at_str[:-1] + "+00:00"
                dt_obj = datetime.fromisoformat(created_at_str)
                # Ensure it's timezone-aware (assume UTC if naive)
                if dt_obj.tzinfo is None or dt_obj.tzinfo.utcoffset(dt_obj) is None:
                     dt_obj = dt_obj.replace(tzinfo=timezone.utc)
                created_at_ts = int(dt_obj.timestamp())
            except ValueError:
                print(f"Warning: Could not parse createdAt timestamp '{created_at_str}'. Using current time.")
                created_at_ts = int(time.time())
            except Exception as e:
                print(f"Warning: Error converting createdAt timestamp '{created_at_str}': {e}. Using current time.")
                created_at_ts = int(time.time())
        elif created_at_str: # If it exists but isn't a string, log warning
            print(f"Warning: Unexpected type for createdAt: {type(created_at_str)}. Using current time.")
            created_at_ts = int(time.time())
        else: # If it doesn't exist, use current time
             created_at_ts = int(time.time())


        if msg_type == "TextMessage":
            if msg_id in tool_calls_by_parent_id: continue
            if content is None: content = ""
            pass

        elif msg_type == "ActionExecutionMessage":
            continue # Handled in first pass

        elif msg_type == "ResultMessage":
            role = "tool"
            tool_call_id = msg.get("actionExecutionId")
            content = msg.get("result", "")
            tool_name = msg.get("actionName")

        # Create potential assistant message if it's a parent of tool calls
        if msg_id in tool_calls_by_parent_id and msg_id not in created_assistant_messages:
             agno_messages.append(AgnoMessage(
                 role="assistant",
                 content=content or "",
                 id=msg_id,
                 tool_calls=tool_calls_by_parent_id[msg_id],
                 tool_call_id=None,
                 name=None,
                 created_at=created_at_ts # Pass integer timestamp
             ))
             created_assistant_messages.add(msg_id)
             continue

        # Create other message types if not already handled
        if msg_type != "ActionExecutionMessage" and msg_id not in created_assistant_messages:
            if content is not None and not isinstance(content, str): content = str(content)
            try:
                agno_msg = AgnoMessage(
                    role=role,
                    content=content,
                    id=msg_id,
                    tool_calls=tool_calls,
                    tool_call_id=tool_call_id,
                    name=tool_name,
                    created_at=created_at_ts # Pass integer timestamp
                )
                agno_messages.append(agno_msg)
            except Exception as e:
                print(f"Warning: Failed to create AgnoMessage from CopilotKit message: {msg}. Error: {e}")
                # Log the validation error details if available
                if hasattr(e, 'errors'):
                    print(f"Validation Errors: {e.errors()}") # type: ignore

    return agno_messages

def agno_messages_to_copilotkit(agno_messages: Sequence[AgnoMessage]) -> List[CopilotKitMessage]:
    """Converts Agno messages back to CopilotKit format, handling timestamps."""
    copilotkit_messages: List[Dict[str, Any]] = []
    processed_tool_calls: Set[str] = set()

    for msg in agno_messages:
        created_at_iso: Optional[str] = None
        if hasattr(msg, 'created_at') and msg.created_at is not None:
            try:
                # Convert integer timestamp back to ISO string (UTC)
                dt_obj = datetime.fromtimestamp(msg.created_at, tz=timezone.utc)
                created_at_iso = dt_obj.isoformat().replace('+00:00', 'Z')
            except Exception as e:
                print(f"Warning: Could not convert Agno timestamp {msg.created_at} back to ISO string: {e}")
                created_at_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z') # Fallback
        else:
            # Fallback if created_at is missing
            created_at_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


        base_msg: Dict[str, Any] = {
            "id": getattr(msg, 'id', str(uuid.uuid4())),
            "createdAt": created_at_iso # Use ISO string
        }

        # --- (Rest of the logic remains the same as before) ---
        if msg.role == "tool" and msg.tool_call_id:
            copilotkit_msg = {
                **base_msg,
                "type": "ResultMessage",
                "actionExecutionId": msg.tool_call_id,
                "actionName": msg.tool_name or "unknown_tool",
                "result": msg.get_content_string() or "",
            }
            copilotkit_messages.append(cast(CopilotKitMessage, copilotkit_msg))
        elif msg.role == "assistant" and msg.tool_calls:
            # Create separate ActionExecutionMessage for each tool call
            for tool_call in msg.tool_calls:
                 tool_call_id = tool_call.get('id')
                 if tool_call_id and tool_call_id not in processed_tool_calls:
                     try: arguments = json.loads(tool_call.get('function', {}).get('arguments', '{}'))
                     except json.JSONDecodeError: arguments = {}

                     copilotkit_msg = {
                         "id": tool_call_id,
                         "createdAt": base_msg["createdAt"], # Reuse parent timestamp
                         "type": "ActionExecutionMessage",
                         "name": tool_call.get('function', {}).get('name', 'unknown_function'),
                         "arguments": arguments,
                         "parentMessageId": base_msg["id"],
                     }
                     copilotkit_messages.append(cast(CopilotKitMessage, copilotkit_msg))
                     processed_tool_calls.add(tool_call_id)
            # Append the main assistant message if it has text content
            assistant_content = msg.get_content_string()
            if assistant_content:
                 copilotkit_text_msg = {
                      **base_msg,
                      "type": "TextMessage",
                      "role": msg.role,
                      "content": assistant_content,
                      "parentMessageId": None,
                 }
                 if base_msg["id"] in processed_tool_calls:
                     copilotkit_text_msg["id"] = str(uuid.uuid4())
                 copilotkit_messages.append(cast(CopilotKitMessage, copilotkit_text_msg))

        elif msg.role in ["user", "assistant", "system"]:
            content_str = msg.get_content_string()
            if content_str is None: content_str = ""
            copilotkit_msg = {
                **base_msg,
                "type": "TextMessage",
                "role": msg.role,
                "content": content_str,
            }
            copilotkit_messages.append(cast(CopilotKitMessage, copilotkit_msg))
        else:
            print(f"Warning: Skipping Agno message with unmappable role: {msg.role}")
            continue

    return copilotkit_messages


# --- Event Mapping ---
# (map_agno_chunk_to_copilotkit_protocol_events remains the same)
def map_agno_chunk_to_copilotkit_protocol_events(
    chunk: AgnoRunResponse,
    is_frontend_action: bool = False,
) -> List[RuntimeProtocolEvent]:
    """
    Maps an AgnoRunResponse chunk to CopilotKit PROTOCOL events.
    """
    events: List[RuntimeProtocolEvent] = []
    event_type = chunk.event

    # 1. Handle Text Content (only if it's the main event type)
    if chunk.content and event_type == AgnoRunEvent.run_response.value:
        events.append(TextMessageContent(type=RuntimeEventTypes.TEXT_MESSAGE_CONTENT, messageId="PLACEHOLDER_ID", content=str(chunk.content))) # type: ignore

    # 2. Handle Tool Calls
    if chunk.tools:
        for tool_call in chunk.tools:
            tool_call_id = tool_call.get("tool_call_id", str(uuid.uuid4()))
            tool_name = tool_call.get("tool_name", "unknown_tool")
            tool_args = tool_call.get("tool_args", {})
            tool_result = tool_call.get("content") # Result is in 'content' for completed calls

            if is_frontend_action:
                events.append(ActionExecutionStart(type=RuntimeEventTypes.ACTION_EXECUTION_START, actionExecutionId=tool_call_id, actionName=tool_name, parentMessageId=None)) # type: ignore
                events.append(ActionExecutionArgs(type=RuntimeEventTypes.ACTION_EXECUTION_ARGS, actionExecutionId=tool_call_id, args=json.dumps(tool_args))) # type: ignore
            elif tool_result is not None and event_type == AgnoRunEvent.tool_call_completed.value:
                 events.append(ActionExecutionResult(type=RuntimeEventTypes.ACTION_EXECUTION_RESULT, actionExecutionId=tool_call_id, actionName=tool_name, result=str(tool_result))) # type: ignore

    return events

# --- State Filtering ---
# (filter_agno_state remains the same)
def filter_agno_state(state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Filters sensitive or unnecessary state before sending to frontend."""
    if state is None:
        return {}
    internal_keys = {"_internal_variable", "some_other_private_key"}
    filtered = {
        k: v for k, v in state.items()
        if not k.startswith("_") and k not in internal_keys
    }
    return filtered