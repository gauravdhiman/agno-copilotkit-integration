# CopilotKit React Hooks: Frontend Documentation for Backend Integration

This documentation details the primary React hooks provided by `@copilotkit/react-core`. It explains their purpose, usage, internal mechanisms, and critically, their dependencies on backend communication, aiming to guide developers integrating CopilotKit with custom backends like Agno.

**Core Concept: The Copilot Context**

All CopilotKit hooks operate within the context provided by the `<CopilotKit>` component. This context acts as a central hub, managing:

1.  **API Configuration (`copilotApiConfig`):** Backend endpoint URLs, authentication headers, and custom properties passed from the `<CopilotKit>` props.
2.  **Shared State:** Includes loading indicators, registered actions (from `useCopilotAction`), agent states (from `useCoAgent`), context strings (from `useCopilotReadable`), the active agent session, etc.
3.  **Core Functions:** Methods exposed by the context to interact with its state (e.g., `setAction`, `addContext`, `setAgentSession`).
4.  **Runtime Client (`runtimeClient`):** An instance of `CopilotRuntimeClient` from `@copilotkit/runtime-client-gql`. This client handles all GraphQL communication (mutations and queries) with the backend endpoint defined in `runtimeUrl`.
5.  **Messages State:** Managed separately via `CopilotMessagesContext` to optimize re-renders, holding the array of current chat `Message` objects.

Hooks read data from this context and use its functions and the `runtimeClient` to interact with the backend.

---

## Hook Reference

### 1. `useCopilotChat`

*   **Purpose:** The primary hook for building chat interfaces. Manages message state, submits user input, handles streaming responses (text, actions, agent state), and provides chat interaction controls.
*   **Prerequisites:** Must be used within a `<CopilotKit>` provider.
*   **Parameters:** `UseCopilotChatOptions` (optional)
    *   `id`: Unique ID for the chat instance.
    *   `initialMessages`: `Message[]` - Array to prepopulate the chat.
    *   `makeSystemMessage`: `(contextString: string, additionalInstructions?: string) => string` - Function to generate the system prompt using context from `useCopilotReadable` and `useCopilotAdditionalInstructions`.
*   **Return Value:** `UseCopilotChatReturn`
    *   `visibleMessages`: `Message[]` - The current array of messages for display.
    *   `appendMessage`: `(message: Message, options?: { followUp?: boolean }) => Promise<void>` - Adds a message (typically user input) and triggers a backend request unless `followUp` is `false`.
    *   `reloadMessages`: `(messageId: string) => Promise<void>` - Resubmits messages up to the specified `messageId` to regenerate the subsequent response.
    *   `stopGeneration`: `() => void` - Aborts the current backend request.
    *   `isLoading`: `boolean` - Indicates if a backend request is in progress.
    *   `setMessages`: `(messages: Message[]) => void` - Directly sets the entire message list.
    *   `deleteMessage`: `(messageId: string) => void` - Removes a message by ID.
    *   `reset`: `() => void` - Clears messages and agent state.
    *   `runChatCompletion`: `() => Promise<Message[]>` - Manually triggers a backend request with the current message history.
*   **Usage Example:**
    ```tsx
    import { useCopilotChat } from "@copilotkit/react-core";
    import { Role, TextMessage } from "@copilotkit/runtime-client-gql";

    function ChatInput() {
      const [text, setText] = useState("");
      const { appendMessage, isLoading, stopGeneration } = useCopilotChat();

      const sendMessage = () => {
        appendMessage(new TextMessage({ content: text, role: Role.User }));
        setText("");
      };

      return (
        <div>
          {/* ... message display ... */}
          <input value={text} onChange={(e) => setText(e.target.value)} disabled={isLoading} />
          <button onClick={isLoading ? stopGeneration : sendMessage}>
            {isLoading ? "Stop" : "Send"}
          </button>
        </div>
      );
    }
    ```
*   **Internal Workings:**
    *   Gets API config, actions, agent state, context functions from `useCopilotContext`.
    *   Gets/sets messages via `useCopilotMessagesContext`.
    *   Uses the internal `useChat` hook to manage the request lifecycle.
    *   `appendMessage`/`reloadMessages`/`runChatCompletion` gather context (messages, actions registered via `useCopilotAction`, context from `useCopilotReadable`, agent state from `useCoAgent`, forwarded parameters from `<CopilotKit>`) and call `runtimeClient.generateCopilotResponse`.
    *   Processes the streamed GraphQL response:
        *   Appends text chunks (`TextMessageOutput`) to the current assistant message.
        *   Handles `ActionExecutionMessageOutput` by finding the corresponding `render` function (from `useCopilotAction`) in `chatComponentsCache` or triggering the `handler` (if frontend action).
        *   Handles `AgentStateMessageOutput` by updating the agent's state in the context (used by `useCoAgent`) and potentially triggering UI renders (via `useCoAgentStateRender`).
        *   Handles `ResultMessageOutput`, updating the status of the corresponding action.
        *   Handles `MetaEvent` for special cases like LangGraph interrupts.
    *   `stopGeneration` uses the `AbortController` from context (`chatAbortControllerRef`) passed to the `runtimeClient`'s fetch call.
*   **Server Dependencies:**
    *   **Sends:** GraphQL Mutation `generateCopilotResponse`.
        *   **Input:** `GenerateCopilotResponseInput` containing:
            *   `messages`: Current history (`MessageInput[]`, excluding AgentStateMessages).
            *   `frontend.actions`: List of actions registered via `useCopilotAction` (`ActionInput[]`).
            *   `frontend.url`: Current window URL.
            *   `agentSession`: Current agent info (`AgentSessionInput`) if an agent is active (`useCoAgent`).
            *   `agentStates`: Current states of all agents (`AgentStateInput[]`).
            *   `forwardedParameters`: Parameters like `temperature` (`ForwardedParametersInput`).
            *   `properties`: Custom properties from `<CopilotKit>`.
            *   `threadId`, `runId`: For stateful interactions.
            *   `metaEvents`: For special interactions like LangGraph interrupt responses (`MetaEventInput[]`).
            *   `extensions`: For specific adapter needs (e.g., OpenAI Assistant API thread/run IDs).
    *   **Expects:** Streaming GraphQL response for `generateCopilotResponse`.
        *   **Output:** `CopilotResponse` containing a stream of `messages`. The backend must send the appropriate `OutputType` variants:
            *   `TextMessageOutput` (streamed `content`)
            *   `ActionExecutionMessageOutput` (streamed `arguments`)
            *   `ResultMessageOutput` (`result`)
            *   `AgentStateMessageOutput` (`state`, `running`, `active`, `nodeName`, etc.)
            *   `BaseMetaEvent` (for interrupts, etc.)
        *   Also expects `threadId`, `runId` (optional), `status`, and `extensions` (optional).

---

### 2. `useCopilotAction`

*   **Purpose:** Defines frontend or backend functions (actions) callable by the AI, potentially with custom UI rendering in the chat.
*   **Prerequisites:** Must be used within a `<CopilotKit>` provider.
*   **Parameters:** `FrontendAction<T>` | `CatchAllFrontendAction`
    *   `name`: Unique action name (or `"*"` for catch-all).
    *   `description`: Natural language description for the AI.
    *   `parameters`: `Parameter[]` - Array describing expected arguments.
    *   `handler`: Function executed when the action is called (can be async). Receives parsed arguments.
    *   `render`: Function to render UI in the chat (`(props: ActionRenderProps<T>) => string | React.ReactElement`). Receives `status`, `args`, `result`.
    *   `renderAndWaitForResponse`: Renders interactive UI. Receives `respond` callback (`(props: ActionRenderPropsWait<T>) => React.ReactElement`). The backend *pauses* until `respond` is called.
    *   `available`: `'enabled' | 'disabled' | 'remote' | 'frontend'` - Controls execution location. Default: 'remote' if handler exists, 'frontend' otherwise.
    *   `pairedAction`: Name of another action to execute after this one.
    *   `followUp`: `boolean` (default: `true`) - Whether the backend should continue after receiving the result.
*   **Return Value:** `void`.
*   **Usage Example:**
    ```tsx
    import { useCopilotAction } from "@copilotkit/react-core";

    function MyComponent() {
      useCopilotAction({
        name: "notifyUser",
        description: "Sends a notification to the user.",
        parameters: [{ name: "message", type: "string", required: true }],
        handler: async ({ message }) => {
          alert(`Notification: ${message}`);
        },
        // Optional: Render a status message in chat
        render: ({ status, args }) => {
          if (status === "executing") return `Sending notification: "${args.message}"...`;
          if (status === "complete") return `Notification sent: "${args.message}"`;
          return null; // Don't render during 'inProgress' (arguments streaming)
        },
      });

      return <div>...</div>;
    }
    ```
*   **Internal Workings:**
    *   Registers the action definition (name, description, parameter schema) in `CopilotContext.actions` via `setAction` on mount/dependency change.
    *   Stores the `render` function in `CopilotContext.chatComponentsCache`.
    *   If `renderAndWaitForResponse` is used, it wraps it internally using a Promise, making the `handler` wait for the promise resolution triggered by the `respond` call.
    *   Removes the action via `removeAction` on unmount.
*   **Server Dependencies:**
    *   **Sends:** Action definition (`name`, `description`, parameter `jsonSchema`) sent *to* the backend with each `generateCopilotResponse` call.
    *   **Receives:** Expects an `ActionExecutionMessageOutput` *from* the backend when the AI decides to call this action. Arguments are streamed.
    *   **Frontend Execution:**
        *   `handler`/`render`: Triggered by `useCopilotChat` upon receiving `ActionExecutionMessageOutput`.
        *   `renderAndWaitForResponse`: When `respond(result)` is called, `useCopilotChat` implicitly includes a `ResultMessageInput` with the `actionExecutionId` and `result` in the *next* request *to* the backend (if `followUp: true`).
    *   **Backend Execution:** If `available` is not `'frontend'`, the backend receives the `ActionExecutionMessageOutput`, executes its own logic, and should send a `ResultMessageOutput` back *to* the frontend with the result. The frontend `render` function (if defined) will still be called based on the message status updates.

---

### 3. `useCopilotReadable` & `useMakeCopilotDocumentReadable`

*   **Purpose:** Provide application state or document content as context to the AI backend.
*   **Prerequisites:** Must be used within a `<CopilotKit>` provider.
*   **Parameters:**
    *   `useCopilotReadable`: `UseCopilotReadableOptions` (`description`, `value`, `parentId?`, `categories?`, `available?`, `convert?`)
    *   `useMakeCopilotDocumentReadable`: `DocumentPointer`, `categories?`
*   **Return Value:** `string | undefined` (The unique ID for the context item).
*   **Usage Example (`useCopilotReadable`):**
    ```tsx
    import { useCopilotReadable } from "@copilotkit/react-core";
    import { useState } from "react";

    function UserProfile({ userId }) {
      const [userName, setUserName] = useState("Alice");
      useCopilotReadable({
        description: `Current user's name (ID: ${userId})`,
        value: userName,
      });
      // ...
    }
    ```
*   **Internal Workings:**
    *   On mount/dependency change, calls `addContext` or `addDocumentContext` from `CopilotContext`.
    *   The context provider formats the data (e.g., `"User Name: Alice"`) and stores it internally, associated with the given categories.
    *   On unmount/dependency change, calls `removeContext` or `removeDocumentContext` to clean up.
*   **Server Dependencies:**
    *   **None directly.** The registered context information is retrieved via `getContextString()` by `useCopilotChat` *before* sending a request.
    *   This context string is typically included in the system message sent *to* the backend. The backend LLM uses this combined context.

---

### 4. `useCoAgent`

*   **Purpose:** Manages the state and lifecycle for a specific backend AI agent (CoAgent), enabling interactive, stateful experiences.
*   **Prerequisites:** `<CopilotKit>`, a compatible backend agent implementation discoverable via `/info`.
*   **Parameters:** `UseCoagentOptions<T>` (`name`, `initialState?`, `state?`, `setState?`, `configurable?`)
*   **Return Value:** `UseCoagentReturnType<T>` (`name`, `nodeName?`, `threadId?`, `running`, `state`, `setState`, `start`, `stop`, `run`)
*   **Usage Example:**
    ```tsx
    import { useCoAgent } from "@copilotkit/react-core";

    type CounterState = { count: number };

    function AgentController() {
      const agent = useCoAgent<CounterState>({
        name: "counterAgent",
        initialState: { count: 0 },
      });

      return (
        <div>
          <p>Agent: {agent.name}</p>
          <p>Node: {agent.nodeName || "N/A"}</p>
          <p>State: {JSON.stringify(agent.state)}</p>
          <p>Running: {agent.running ? "Yes" : "No"}</p>
          <button onClick={agent.start}>Start Agent</button>
          <button onClick={agent.stop}>Stop Agent</button>
          <button onClick={() => agent.run()}>Run</button>
          <button onClick={() => agent.setState(s => ({ ...s, count: (s?.count ?? 0) + 1 }))}>
            Increment Client State
          </button>
        </div>
      );
    }
    ```
*   **Internal Workings:**
    *   Manages the active agent via `agentSession` in `CopilotContext`.
    *   Stores and updates the agent's specific state in `CopilotContext.coagentStates`.
    *   `start` sets the `agentSession`.
    *   `stop` clears the `agentSession` and resets local flags.
    *   `run` uses `useCopilotChat`'s `appendMessage` or `runChatCompletion` to send the current state and trigger backend execution.
    *   Listens for `AgentStateMessage` events (via `useCopilotChat`) matching its `name` to update local state (`state`, `nodeName`, `running`, etc.).
    *   Calls `runtimeClient.loadAgentState` on mount/`threadId` change to sync with backend persistence.
*   **Server Dependencies:**
    *   **Discovery:** Expects agent `name` and `description` from the backend's `/info` endpoint response.
    *   **Execution:** Sends `agentSession` and `agentStates` in the `generateCopilotResponse` mutation.
    *   **State Updates:** Expects the backend to stream `AgentStateMessageOutput` events with `state`, `running`, `active`, `nodeName`, `threadId`, `runId`.
    *   **Persistence:** Requires a backend implementation for the `loadAgentState` GraphQL query.

---

### 5. `useCoAgentStateRender`

*   **Purpose:** Renders custom UI based on `AgentStateMessage` updates from a specific backend agent node.
*   **Prerequisites:** `<CopilotKit>`, a running backend agent sending `AgentStateMessage` updates.
*   **Parameters:** `CoAgentStateRender<T>` (`name`, `nodeName?`, `handler?`, `render?`)
*   **Return Value:** `void`.
*   **Usage Example:**
    ```tsx
    import { useCoAgentStateRender } from "@copilotkit/react-core";

    type SearchState = { progress: number; status: string };

    function SearchStatusDisplay() {
      useCoAgentStateRender<SearchState>({
        name: "searchAgent",
        nodeName: "performingSearch", // Optional: only render for this node
        render: ({ state, status }) => {
          if (status === "inProgress") {
            return `Searching... Progress: ${state.progress}% (${state.status})`;
          }
          return "Search complete.";
        },
      });
      return null; // Hook handles rendering within the chat list
    }
    ```
*   **Internal Workings:**
    *   Registers `render`/`handler` functions in `CopilotContext.chatComponentsCache` keyed by `"agentName-nodeName"` or `"agentName-global"`.
    *   The `<Messages>` component (used by `<CopilotChat>`) finds and calls the appropriate `render` function from the cache when an `AgentStateMessage` is encountered in the message list.
*   **Server Dependencies:**
    *   Relies entirely on the backend streaming `AgentStateMessageOutput` events via the `generateCopilotResponse` stream. The message must contain the correct `agentName`, `nodeName`, and `state` payload.

---

### 6. `useLangGraphInterrupt` / `useLangGraphInterruptRender`

*   **Purpose:** Handles LangGraph's `interrupt` mechanism, allowing UI rendering and response submission back to the paused agent.
*   **Prerequisites:** `<CopilotKit>`, LangGraph backend agent using `interrupt` or `copilotkit_interrupt`.
*   **Parameters (`useLangGraphInterrupt`):** `Omit<LangGraphInterruptRender<T>, "id">` (`render`, `handler?`, `enabled?`)
*   **Return Value (`useLangGraphInterruptRender`):** `string | React.ReactElement | null`.
*   **Usage Example:**
    ```tsx
    import { useLangGraphInterrupt, useLangGraphInterruptRender } from "@copilotkit/react-core";
    import React from "react";

    type ConfirmationEvent = { taskId: string; taskDetails: string };

    function LangGraphInteraction() {
      // Define how to handle the interrupt
      useLangGraphInterrupt<ConfirmationEvent>({
        render: ({ event, resolve }) => (
          <div>
            <p>Please confirm task: {event.value.taskDetails}</p>
            <button onClick={() => resolve("CONFIRMED")}>Confirm</button>
            <button onClick={() => resolve("REJECTED")}>Reject</button>
          </div>
        ),
        // Optional: Only handle interrupts where taskId starts with 'abc'
        // enabled: ({ eventValue }) => eventValue.taskId.startsWith('abc'),
      });

      // Get the UI element to render (will be null if no interrupt is active)
      const interruptUI = useLangGraphInterruptRender();

      return <div>{interruptUI /* Render the interrupt UI when active */}</div>;
    }
    ```
*   **Internal Workings:**
    *   `useLangGraphInterrupt` registers the handlers in context. It listens for changes to the `response` field within the context's `langGraphInterruptAction` state. When a response is set (by the `resolve` function), it triggers `runChatCompletion` to send the response back.
    *   `useLangGraphInterruptRender` reads the active interrupt details from context. If an interrupt matching the `enabled` condition is found, it calls the `render` function, passing a `resolve` callback. Calling `resolve(response)` updates the context state.
*   **Server Dependencies:**
    *   **Receives:** A `BaseMetaEvent` of type `LangGraphInterruptEvent` or `CopilotKitLangGraphInterruptEvent` from the backend stream, containing the `value` payload from the interrupting LangGraph node.
    *   **Sends:** When `resolve(response)` is called, the `response` string is sent back *to* the backend in the *next* `generateCopilotResponse` mutation within a `MetaEventInput` object. The backend LangGraph agent uses this response to resume.

---

### 7. `useCopilotAdditionalInstructions`

*   **Purpose:** Dynamically adds instructions to the system prompt based on component state or lifecycle.
*   **Prerequisites:** `<CopilotKit>`.
*   **Parameters:** `UseCopilotAdditionalInstructionsOptions` (`instructions`, `available?`)
*   **Return Value:** `void`.
*   **Usage Example:**
    ```tsx
    import { useCopilotAdditionalInstructions } from "@copilotkit/react-core";
    import { useState } from "react";

    function StrictModeToggle() {
      const [isStrict, setIsStrict] = useState(false);
      useCopilotAdditionalInstructions({
        instructions: "Be extremely concise and only answer the direct question.",
        available: isStrict ? "enabled" : "disabled",
      }, [isStrict]);

      return <label><input type="checkbox" checked={isStrict} onChange={e => setIsStrict(e.target.checked)} /> Strict Mode</label>;
    }
    ```
*   **Internal Workings:**
    *   Manages the `additionalInstructions` array in `CopilotContext` via `setAdditionalInstructions`. Adds instructions on mount/enable, removes on unmount/disable.
*   **Server Dependencies:**
    *   **None directly.** Instructions are gathered by `useCopilotChat` when constructing the system message sent *to* the backend.

---

### 8. `useCopilotRuntimeClient`

*   **Purpose:** Provides direct access to the underlying GraphQL client (`CopilotRuntimeClient`) for advanced use cases or direct backend interaction outside the standard chat flow.
*   **Prerequisites:** `<CopilotKit>`.
*   **Parameters:** `CopilotRuntimeClientOptions` (usually derived from context).
*   **Return Value:** `CopilotRuntimeClient` instance.
*   **Internal Workings:** Memoized client instance using config from context. Integrates with `useToast` for error/warning display.
*   **Server Dependencies:** The returned client is used by other hooks to make GraphQL calls (`generateCopilotResponse`, `availableAgents`, `loadAgentState`).

---

### 9. `useCopilotAuthenticatedAction_c` (Cloud Feature)

*   **Purpose:** Wraps `useCopilotAction` to require Copilot Cloud authentication before execution/rendering. Renders a sign-in component if not authenticated.
*   **Prerequisites:** `<CopilotKit>` with `publicApiKey` and `authConfig_c`.
*   **Parameters:** `FrontendAction<T>`, `dependencies?`.
*   **Return Value:** `void`.
*   **Internal Workings:** Uses `useCopilotAction` internally. Its wrapped `render` function checks `CopilotContext.authStates_c`. If unauthenticated, renders the `SignInComponent` provided in `authConfig_c`, storing the pending action. `onSignInComplete` updates auth state and triggers the stored action.
*   **Server Dependencies:** Same as `useCopilotAction`. Authentication state is managed client-side based on interactions with the `SignInComponent`.

---

## Backend Integration Summary (for Agno)

To integrate Agno as a backend for CopilotKit based on these frontend hooks, you need to implement:

1.  **GraphQL Endpoint:** Adhere to the schema in `@copilotkit/runtime/__snapshots__/schema/schema.graphql`.
2.  **`generateCopilotResponse` Mutation:**
    *   Handle incoming `messages`, `actions`, `agentSession`, `agentStates`, `properties`, `forwardedParameters`, `metaEvents`.
    *   If `agentSession` present: Route to the corresponding Agno Agent/Workflow. Manage state based on `threadId`, `nodeName`, and received `agentStates`. Pass available `actions` for tool use. Handle resumption based on `metaEvents` (for interrupts).
    *   If no `agentSession`: Handle as a direct LLM call or simpler workflow.
    *   **Crucially:** Stream back `CopilotResponse` with the correct `OutputType` messages (`TextMessageOutput`, `ActionExecutionMessageOutput`, `ResultMessageOutput`, `AgentStateMessageOutput`, `BaseMetaEvent`) using `@stream`/`@defer`.
    * Receive `messages`, `actions`, `agentSession`, `agentStates`, `properties`, `forwardedParameters`, `metaEvents`.
    * **Core Logic**: Based on the input, decide what to do:
        * If `agentSession` is present and you're handling agents directly (not using @copilotkit/runtime's agent handling): Engage the specified Agno Agent/Workflow (agentSession.agentName), potentially resuming from a `threadId` and `nodeName`. Pass it the `messages`, available `actions` (for tool calling), current `state` (from `agentStates`), `properties`, and `forwardedParameters`.
        * If no `agentSession` (or using @copilotkit/runtime which delegates to an adapter): Process as a standard chat completion request, potentially using an LLM directly or a simpler workflow. Pass `messages`, `actions`, `properties`, `forwardedParameters`.
        * **Streaming Response**: Your handler must stream back results using the defined GraphQL @stream and @defer directives. This involves sending back chunks corresponding to the different OutputTypes (`TextMessageOutput`, `ActionExecutionMessageOutput`, `ResultMessageOutput`, `AgentStateMessageOutput`, `MetaEvent`).
        * **Agent State Updates**: If handling agents, periodically stream `AgentStateMessageOutput` containing the current `state` (JSON string), running `status`, active `status`, and `nodeName` to update the frontend UI via `useCoAgent` and `useCoAgentStateRender`.
        * **Action Execution Requests**: When the Agno agent/workflow decides to call a tool/`action` defined by the frontend, stream an `ActionExecutionMessageOutput` with the `name` and streamed `arguments`.
        * **Handling Frontend Action Results**: If the frontend executes an action (especially via `renderAndWaitForResponse`) and sends back a `ResultMessageInput` in the next request, your backend needs to receive this result and pass it back to the waiting Agno agent/workflow step.
        * **Handling Interrupts**: If you want to support interrupts similar to LangGraph, your Agno agent needs a mechanism to pause and signal this pause. You'd then stream back a `MetaEvent` (e.g., `LangGraphInterruptEvent`). When the frontend sends the response back in a subsequent request's `MetaEventInput`, your backend needs to resume the Agno agent/workflow with that response.
3.  **`/info` Equivalent:** An endpoint (can be part of GraphQL or separate REST) that returns available server-side `actions` and discoverable Agno `agents` (name, description).
4.  **`loadAgentState` Query:** An endpoint to retrieve persisted state (`state` JSON string) and `messages` (JSON string) for a given `threadId` and `agentName`.

By fulfilling these server-side requirements, you can leverage the CopilotKit React hooks with your Agno backend.