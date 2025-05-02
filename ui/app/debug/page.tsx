// ui/app/test/page.tsx
"use client"; // Required for hooks

import React, { useMemo, useState } from "react";
import { useCopilotAction, useCopilotChat, useCoAgentStateRender } from "@copilotkit/react-core";
import { CopilotSidebar } from "@copilotkit/react-ui"; // Import the sidebar
import { Message } from "@copilotkit/runtime-client-gql"; // Import base Message type
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "../components/ui/accordion"; // Ensure correct path
import ReactMarkdown from "react-markdown";

// Define the types expected in the state payload from the backend
// Ensure this matches the Pydantic model in your backend utils.py
interface TimelineEvent {
  timestamp: string;
  event_type: string;
  event_summary: string;
  event_details?: string;
}

interface AgentStateFromBackend {
  event_timeline?: TimelineEvent[];
  session_state?: Record<string, any>;
  // Add other state properties if the backend sends them
}

export default function TestAndChatPage() {
  // --- Get visibleMessages from useCopilotChat ---
  // This hook connects to the state managed by the <CopilotKit> provider
  // which is also used by the <CopilotSidebar>
  const { visibleMessages, isLoading } = useCopilotChat();

  // --- Action: Greet User ---
  // This action primarily renders UI in the frontend chat window.
  useCopilotAction({
    name: "greetUser",
    available: "frontend", // Executes in the frontend
    description: "Displays a greeting message to the user in the chat.",
    parameters: [
      {
        name: "name",
        type: "string",
        description: "The name of the user to greet.",
        required: true,
      },
    ],
    // Add a minimal handler that resolves immediately
    // handler: async (args) => {
    //   console.log("Frontend greetUser handler executed (for status change). Args:", args);
    //   // You could potentially do minor UI side-effects here if needed,
    //   // but the main rendering is below.
    //   // Returning a value might be useful if the backend needed confirmation,
    //   // but isn't strictly required for just changing the render status.
    //   await new Promise(resolve => setTimeout(resolve, 5000)); // Wait for 2 seconds
    //   return `Greeting acknowledged for ${args.name}`;
    // },
    // Renders different UI based on the action's status
    render: ({ args, status }) => {
      if (status === "complete") {
         return <div className="text-md bg-blue-100 text-blue-800 p-2 rounded-lg my-2">Greeted {args.name}!</div>;
      }
      if (status === "executing") {
        return <div className="text-md italic text-gray-600 p-2">Greeting {args.name}...</div>;
      }
      if (status === "inProgress") { // Optionally handle argument streaming phase
        return <div className="text-md italic text-gray-500 p-2">Preparing greeting...</div>;
      }
      return <></>; // Return empty fragment if not executing or complete
    },
    // USE renderAndWaitForResponse instead
    // renderAndWaitForResponse: ({ args, status, respond }) => {
    //   console.log(`greetUser renderAndWait status: ${status}`);
    //   respond?.(`Greeting displayed for ${args.name}`);

    //   // When the framework signals execution has started (arguments are ready)
    //   if (status === "executing") {
    //     // Immediately signal completion (since there's no user input needed)
    //     // Pass back some simple confirmation data if desired
    //     respond(`Greeting displayed for ${args.name}`);

    //     // Render the "executing" state momentarily
    //     return <div className="text-md italic text-gray-600 p-2">Greeting {args.name}...</div>;
    //   }

    //   // Optionally render something during argument streaming
    //   if (status === "inProgress") {
    //     return <div className="text-md italic text-gray-500 p-2">Preparing greeting...</div>;
    //   }


    //   // NOTE: The 'complete' status is less relevant here because we trigger
    //   // completion via respond(). You might not even reach this in the render
    //   // cycle for this specific component instance after calling respond.
    //   // A different mechanism (like updating a separate state) would be needed
    //   // if you wanted to show a persistent "Greeted!" message *after* completion.
    //   // For simplicity, we'll just return nothing once respond is called.

    //   return <></>;
    // },
  });

  // --- Action: Display tool details on UI ---
  // This action primarily renders UI in the frontend chat window.
    // --- Action: Display Tool Call Details ---
    useCopilotAction({
      name: "display_tool_call_details",
      description: "Displays the details and results of a completed tool call in the chat.",
      // This action is purely for rendering, so 'available' defaults to frontend
      // and no 'handler' is needed.
      parameters: [
        { name: "tool_call_summary", type: "string", description: "One liner summary of tool call.", required: false },
        { name: "tool_name", type: "string", description: "Name of the tool called", required: true },
        { name: "tool_args", type: "string", description: "Stringified arguments of the tool call", required: false },
        { name: "tool_result", type: "string", description: "Result / outcome of tool call", required: true },
      ],
      render: ({ args, status }) => {
        // Only render when the action execution is fully complete
        if (status === "complete") {
          let parsedArgs: any = null;
          try {
            // Attempt to parse the arguments string for pretty printing
            if (args.tool_args?.startsWith('[') || args.tool_args?.startsWith('{')) {
              parsedArgs = JSON.parse(args.tool_args);
            } else {
              parsedArgs = args.tool_args;
            }
          } catch (e) {
            console.error("Failed to parse tool_args JSON:", args.tool_args);
            // Keep args as string if parsing fails
            parsedArgs = args.tool_args;
          }
  
          return (
            <div className="w-full max-w-[85%] mr-auto my-2"> {/* Align with assistant messages */}
              <Accordion type="single" collapsible className="w-full">
                <AccordionItem value="item-1" className="border bg-white rounded-md overflow-hidden shadow-sm text-xs border-gray-200">
                  <AccordionTrigger className="hover:no-underline px-3 py-2 hover:bg-gray-50 transition-colors w-full text-left">
                    {/* Accordion Trigger: Tool Name and Status */}
                    <div className="flex items-center gap-2 w-full">
                      {/* Checkmark Icon for completion */}
                      <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-green-600 shrink-0"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
                      <span className="font-medium text-gray-800 capitalize-first">
                        Tool Executed: {args.tool_name?.replace(/_/g, ' ')}
                      </span>
                    </div>
                  </AccordionTrigger>
                  <AccordionContent className="px-3 pb-3 pt-1">
                    {/* Accordion Content: Arguments and Result */}
                    <div className="text-black bg-white rounded p-3 prose prose-xs max-w-none border mt-2 space-y-3 shadow-sm">
                      {/* Display Arguments */}
                      <div>
                        <strong className="text-black text-sm">Arguments:</strong>
                        <pre className="text-xs bg-white p-2.5 mt-2 rounded-md overflow-x-auto font-mono border border-gray-200 text-black shadow-sm">
                          {typeof parsedArgs === 'object' ? JSON.stringify(parsedArgs, null, 2) : parsedArgs || "(No arguments)"}
                        </pre>
                      </div>
                      {/* Display Result */}
                      <div>
                        <strong className="text-black text-sm">Result:</strong>
                        <div className="mt-2 prose prose-xs max-w-none bg-white rounded-md p-2.5 border border-gray-200 text-black shadow-sm">
                          <ReactMarkdown>{args.tool_result || "(No result)"}</ReactMarkdown>
                        </div>
                      </div>
                    </div>
                  </AccordionContent>
                </AccordionItem>
              </Accordion>
            </div>
          );
        }
  
        // Optionally show something during execution/inProgress,
        // but the request asked specifically for rendering when complete.
        // if (status === "executing") {
        //   return <div className="text-xs italic text-gray-500 p-1 my-1">Executing tool: {args.tool_name}...</div>;
        // }
  
        // Return empty fragment for other statuses
        return <></>;
      },
    });
  // --- End of Action Hook to display Tool calling details

  // --- Agent State Renderer Hook ---
  // This hook listens for AgentStateMessages from the specified agent
  // and renders the timeline directly into the chat message flow.
  useCoAgentStateRender<AgentStateFromBackend>({
    name: "agno_agent", // MUST match the agent name in the backend adapter
    // Below reder funciton is to show only the latest state of an agent, not complete timeline
    // render: ({ state, status, nodeName }) => {
    //   // Get the *very last* event from the timeline *in this specific state update*
    //   const timelineEvents = state?.event_timeline || [];
    //   const lastEvent = timelineEvents.length > 0 ? timelineEvents[timelineEvents.length - 1] : null;
  
    //   // Render only information about THIS specific state update
    //   if (lastEvent) {
    //      return (
    //        <div className={`my-1 p-2 border text-xs rounded ${status === 'inProgress' ? 'border-blue-200 bg-blue-50' : 'border-gray-300 bg-gray-50'}`}>
    //          <strong>[{nodeName || 'Run Step'} - {status}]</strong> {lastEvent.event_summary}
    //          {lastEvent.event_details && <pre className="text-xs bg-gray-100 p-1 mt-1 rounded overflow-x-auto">{lastEvent.event_details}</pre>}
    //        </div>
    //      );
    //   }
    //   return <></>; // Return empty fragment if no relevant info
    // }

    // Below reder funciton is to show complete timeline of events during agent run
    render: ({ state, status, nodeName }) => {
      // state is the parsed JSON payload from the AgentStateMessage
      const timelineEvents = state?.event_timeline || [];

      // State to manage the open accordion item (defaults to the last item)
      const lastItemValue = timelineEvents.length > 0 ? `item-${timelineEvents.length - 1}` : undefined;
      const [openItem, setOpenItem] = useState<string | undefined>(lastItemValue);

      // Memoize the timeline rendering for potential performance optimization
      const renderedTimeline = useMemo(() => {
        if (timelineEvents.length > 0) {
          return (
            <Accordion type="single" collapsible value={openItem} onValueChange={setOpenItem} className="w-full space-y-1">
              {timelineEvents.map((event, index) => {
                const itemValue = `item-${index}`;
                return (
                  <AccordionItem key={event.timestamp || index} value={itemValue} className="border bg-gray-50/60 rounded-md overflow-hidden transition-shadow hover:shadow-sm text-xs">
                    <AccordionTrigger className="hover:no-underline px-3 py-1.5 hover:bg-gray-100/70 transition-colors w-full text-left">
                      {/* Accordion Trigger content */}
                      <div className="flex flex-col items-start gap-0.5 w-full text-left mr-2">
                        <div className="font-medium text-gray-700 capitalize-first">
                          {event.event_type?.replace(/_/g, ' ')}
                        </div>
                        <div className="text-gray-500 text-xs truncate w-full">
                          {event.event_summary}
                        </div>
                      </div>
                    </AccordionTrigger>
                    {/* Accordion Content (details) */}
                    {event.event_details && (
                      <AccordionContent className="px-2 pb-2 pt-0">
                        <div className="text-gray-600 bg-white rounded p-2 prose prose-xs max-w-none prose-pre:bg-gray-700 prose-pre:text-gray-200 prose-code:text-indigo-700 border-t mt-1 pt-1">
                          <ReactMarkdown>{event.event_details}</ReactMarkdown>
                        </div>
                      </AccordionContent>
                    )}
                  </AccordionItem>
                );
              })}
            </Accordion>
          );
        }
        return null; // Return null if no events to render
      }, [timelineEvents, openItem, setOpenItem]); // Dependencies for memoization


      // Render the memoized timeline within a container div inside the chat
      if (renderedTimeline) {
          return (
            <div className={`my-2 p-2 border rounded ${status === 'inProgress' ? 'border-blue-300 animate-pulse' : 'border-green-300'} bg-white shadow-sm`}>
                 <div className="text-xs text-gray-400 italic mb-1">Agent Status ({status} - Node: {nodeName})</div>
                 {renderedTimeline}
            </div>
          );
      }

      // Fallback if no timeline events in this specific state update message
      return <></>;
    },
  });
  // --- End of CoAgentStateRender Hook ---

  // --- Create a simplified version for display ---
  // const simplifiedMessages = useMemo(() => {
  //   return visibleMessages.map((msg: Message) => { // Add type annotation for safety
  //     let simplified: Record<string, any> = {
  //       id: msg.id,
  //       type: msg.constructor.name,
  //       // @ts-ignore
  //       role: msg.role || undefined,
  //       createdAt: msg.createdAt,
  //     };
  //     if (msg.isTextMessage()) {
  //       simplified.content = msg.content.substring(0, 80) + (msg.content.length > 80 ? "..." : "");
  //     } else if (msg.isActionExecutionMessage()) {
  //       simplified.name = msg.name;
  //       simplified.args = JSON.stringify(msg.arguments).substring(0, 80) + "...";
  //     } else if (msg.isResultMessage()) {
  //       simplified.actionName = msg.actionName;
  //       simplified.result = msg.result.substring(0, 80) + "...";
  //     } else if (msg.isAgentStateMessage()) {
  //       simplified.agentName = msg.agentName;
  //       simplified.nodeName = msg.nodeName;
  //       simplified.running = msg.running;
  //       simplified.active = msg.active;
  //       simplified.stateSummary = msg.state //JSON.stringify(msg.state).substring(0, 80) + "..."; // Truncate state too
  //     }
  //     return simplified;
  //   });
  // }, [visibleMessages]);

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
          Debug: `visibleMessages` Array
        </h2>
        <pre>
          {JSON.stringify(visibleMessages, null, 2)}
        </pre>
      </div>

      {/* Render the Copilot Sidebar for Interaction */}
      {/* It will position itself fixedly based on its CSS */}
      <CopilotSidebar
        defaultOpen={true} // Keep it open by default on this page
        labels={{
          title: "Agno Agent Chat", // Specific title for this page if desired
          initial: "Interact here to see messages in the debug view on left panel.",
        }}
        // Add any other props you need for the sidebar here
      />
    </main>
  );
}