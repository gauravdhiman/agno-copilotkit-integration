# copilotkit_integration/utils.py
import json
import uuid
from typing import Any, Dict, List, Optional, Sequence, cast

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
    # AgentStateMessage is handled by copilotkit_run based on lifecycle events
)

# Import Agno classes
from agno.models.message import Message as AgnoMessage
from agno.run.response import RunResponse as AgnoRunResponse, RunEvent as AgnoRunEvent
from agno.reasoning.step import ReasoningStep # Keep this if you want reasoning steps in protocol

# --- Message Conversion ---
# (copilotkit_messages_to_agno and agno_messages_to_copilotkit remain the same as before)
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
        # --- Refined Tool Call Handling ---
        # Check message type if available (assuming CopilotKit frontend might send typed messages)
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

        # --- Simplified AgnoMessage Creation ---
        # Pass only core fields Agno needs. Avoid passing CopilotKit specific 'type'.
        try:
            agno_msg = AgnoMessage(
                role=role,
                content=content,
                id=msg_id,
                tool_calls=tool_calls,
                tool_call_id=tool_call_id,
                # Add name if CopilotKit provides it and Agno uses it
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
             # Represent tool calls initiated by assistant if needed for state history
             # Usually handled by events during execution, might omit here unless crucial for display
             # Example: Create an ActionExecutionMessage for each tool call in msg.tool_calls
             # For simplicity, often just the assistant message *before* the call is enough
             pass
         elif msg.role in ["user", "assistant", "system"]:
              copilotkit_msg["type"] = "TextMessage"
              # Ensure content is string for TextMessage
              if "content" in copilotkit_msg and not isinstance(copilotkit_msg["content"], str):
                  copilotkit_msg["content"] = str(copilotkit_msg["content"])
         else:
              print(f"Warning: Skipping Agno message with unmappable role: {msg.role}")
              continue # Skip messages with roles CopilotKit doesn't understand directly

         copilotkit_messages.append(cast(CopilotKitMessage, copilotkit_msg))
    return copilotkit_messages

# --- Event Mapping ---

def map_agno_chunk_to_copilotkit_protocol_events(
    chunk: AgnoRunResponse,
) -> List[RuntimeProtocolEvent]:
    """
    Maps an AgnoRunResponse chunk to CopilotKit PROTOCOL events (TextMessage*, ActionExecution*).
    Does NOT map lifecycle/state events.
    """
    events: List[RuntimeProtocolEvent] = []
    event_type = chunk.event

    # Map Content Updates -> Text Messages
    # Only process content if it's part of a standard response event
    if chunk.content and event_type == AgnoRunEvent.run_response.value:
        msg_id = str(uuid.uuid4())
        # events.append(TextMessageStart(type=RuntimeEventTypes.TEXT_MESSAGE_START, messageId=msg_id, parentMessageId=None))
        events.append(TextMessageContent(type=RuntimeEventTypes.TEXT_MESSAGE_CONTENT, messageId=msg_id, content=str(chunk.content)))
        # events.append(TextMessageEnd(type=RuntimeEventTypes.TEXT_MESSAGE_END, messageId=msg_id))

    # Map Tool Call Results (assuming they appear in chunk.tools when completed)
    if chunk.tools: # This likely signifies completed tool calls in AgnoRunResponse
        for tool_call in chunk.tools:
            # Extract info assuming structure like {'tool_call_id': ..., 'tool_name': ..., 'tool_args': ..., 'content': result}
            tool_call_id = tool_call.get("tool_call_id", str(uuid.uuid4()))
            tool_name = tool_call.get("tool_name", "unknown_tool")
            tool_args = tool_call.get("tool_args", {})
            tool_result = tool_call.get("content") # Assuming 'content' holds the result

            # We get completed tool info here. Emit the sequence CopilotKit expects.
            # If Agno provided distinct tool_start/end events, we'd map those separately.
            events.append(ActionExecutionStart(type=RuntimeEventTypes.ACTION_EXECUTION_START, actionExecutionId=tool_call_id, actionName=tool_name, parentMessageId=None))
            events.append(ActionExecutionArgs(type=RuntimeEventTypes.ACTION_EXECUTION_ARGS, actionExecutionId=tool_call_id, args=json.dumps(tool_args)))
            events.append(ActionExecutionEnd(type=RuntimeEventTypes.ACTION_EXECUTION_END, actionExecutionId=tool_call_id))
            if tool_result is not None:
                events.append(ActionExecutionResult(type=RuntimeEventTypes.ACTION_EXECUTION_RESULT, actionExecutionId=tool_call_id, actionName=tool_name, result=str(tool_result)))

    # Map Reasoning Steps (optional, could be a custom event type or part of state)
    # Example: Emit reasoning steps as text messages or custom meta-events
    # if event_type == AgnoRunEvent.reasoning_step.value and chunk.extra_data and chunk.extra_data.reasoning_steps:
    #     step = chunk.extra_data.reasoning_steps[-1]
    #     reasoning_text = f"Thinking Step {step.title}: {step.reasoning} -> Action: {step.action}"
    #     msg_id = str(uuid.uuid4())
    #     events.append(TextMessageStart(type=RuntimeEventTypes.TEXT_MESSAGE_START, messageId=msg_id, parentMessageId=None))
    #     events.append(TextMessageContent(type=RuntimeEventTypes.TEXT_MESSAGE_CONTENT, messageId=msg_id, content=reasoning_text))
    #     events.append(TextMessageEnd(type=RuntimeEventTypes.TEXT_MESSAGE_END, messageId=msg_id))
    #     # Alternatively: events.append(meta_event(name="AgnoReasoningStep", value=step.model_dump()))

    return events # type: ignore

# --- State Filtering ---
# (filter_agno_state remains the same as before)
def filter_agno_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """Filters sensitive or unnecessary state before sending to frontend."""
    filtered = {k: v for k, v in state.items() if not k.startswith("_")}
    # Add more filtering logic as needed
    return filtered