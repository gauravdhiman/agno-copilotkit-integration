// ui/app/test/page.tsx
"use client"; // Required for hooks

import React, { useMemo } from "react";
import { useCopilotChat } from "@copilotkit/react-core";
import { CopilotSidebar } from "@copilotkit/react-ui"; // Import the sidebar
import { Message } from "@copilotkit/runtime-client-gql"; // Import base Message type

// Optional: Define types if you want to inspect specific state parts
// interface TimelineEvent { ... }
// interface AgentStateFromBackend { ... }

export default function TestAndChatPage() {
  // --- Get visibleMessages from useCopilotChat ---
  // This hook connects to the state managed by the <CopilotKit> provider
  // which is also used by the <CopilotSidebar>
  const { visibleMessages, isLoading } = useCopilotChat();

  // --- Create a simplified version for display ---
  const simplifiedMessages = useMemo(() => {
    return visibleMessages.map((msg: Message) => { // Add type annotation for safety
      let simplified: Record<string, any> = {
        id: msg.id,
        type: msg.constructor.name,
        // @ts-ignore
        role: msg.role || undefined,
        createdAt: msg.createdAt,
      };
      if (msg.isTextMessage()) {
        simplified.content = msg.content.substring(0, 80) + (msg.content.length > 80 ? "..." : "");
      } else if (msg.isActionExecutionMessage()) {
        simplified.name = msg.name;
        simplified.args = JSON.stringify(msg.arguments).substring(0, 80) + "...";
      } else if (msg.isResultMessage()) {
        simplified.actionName = msg.actionName;
        simplified.result = msg.result.substring(0, 80) + "...";
      } else if (msg.isAgentStateMessage()) {
        simplified.agentName = msg.agentName;
        simplified.nodeName = msg.nodeName;
        simplified.running = msg.running;
        simplified.active = msg.active;
        simplified.stateSummary = msg.state //JSON.stringify(msg.state).substring(0, 80) + "..."; // Truncate state too
      }
      return simplified;
    });
  }, [visibleMessages]);

  return (
    // Main container for the page content
    <main className="flex flex-col h-screen p-4 md:p-8 bg-gray-100">
      {/* Page Title and Description */}
      <div className="mb-4">
        <h1 className="text-2xl md:text-3xl font-bold mb-2 text-gray-800">Chat & Debug Page</h1>
        <p className="text-gray-600">
          Use the chat sidebar on the right to interact with the agent.
          The debug view below shows the real-time `visibleMessages` array content.
        </p>
        <p className="text-sm mt-1">
          Agent Loading Status:{" "}
          <span className={`font-semibold ${isLoading ? 'text-blue-600 animate-pulse' : 'text-green-600'}`}>
            {isLoading ? "In Progress..." : "Idle"}
          </span>
        </p>
      </div>

      {/* Debug Output Area */}
      <div className="flex-grow bg-gray-900 text-green-400 p-4 rounded-lg shadow-inner overflow-x-auto text-xs font-mono overflow-y-scroll mb-4">
        <h2 className="text-lg font-semibold border-b border-gray-700 pb-1 mb-2 text-white sticky top-0 bg-gray-900 z-10">
          Debug: `visibleMessages` Array (Simplified)
        </h2>
        <pre>
          {JSON.stringify(simplifiedMessages, null, 2)}
        </pre>
      </div>

      {/* Render the Copilot Sidebar for Interaction */}
      {/* It will position itself fixedly based on its CSS */}
      <CopilotSidebar
        defaultOpen={true} // Keep it open by default on this page
        labels={{
          title: "Agno Agent Chat", // Specific title for this page if desired
          initial: "Interact here to see messages in the debug view above.",
        }}
        // Add any other props you need for the sidebar here
      />
    </main>
  );
}