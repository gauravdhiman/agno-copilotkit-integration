// ui/app/page.tsx
"use client";

import { useCopilotAction, useCoAgentStateRender } from "@copilotkit/react-core";
import { CopilotSidebar } from "@copilotkit/react-ui";
import React, { useState, useMemo } from "react"; // Import useMemo
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "./components/ui/accordion"; // Adjust path if needed
import ReactMarkdown from "react-markdown"; // Needed for rendering details

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

export default function Home() {
  return (
    <main>
      <YourMainContent />
      <CopilotSidebar
        defaultOpen={true} // Sidebar starts open
        labels={{
          title: "Agno Agent Assistant", // Title for the chat window
          initial: "Hi! I'm connected to the Agno agent. Ask me math problems or about recent news!", // Initial message
        }}
        // You could pass custom message renderers here if needed later
        // AssistantMessage={CustomAssistantMessage}
        // UserMessage={CustomUserMessage}
        // Messages={CustomMessagesComponentIncludingTimeline} // - This would be the custom UI approach discussed earlier
      />
    </main>
  );
}

// This component contains the main application content and the CopilotKit hooks
function YourMainContent() {
  const [backgroundColor, setBackgroundColor] = useState("#ADD8E6"); // Default light blue background

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
    // Renders different UI based on the action's status
    render: ({ args, status }) => {
      if (status === "complete") {
         return <div className="text-md bg-blue-100 text-blue-800 p-2 rounded-lg my-2">Greeted {args.name}!</div>;
      }
      if (status === "executing") {
        return <div className="text-md italic text-gray-600 p-2">Greeting {args.name}...</div>;
      }
      return <></>; // Return empty fragment if not executing or complete
    },
   });

   // --- Action: Set Background Color ---
   // This action has a handler that executes in the frontend to change component state.
   useCopilotAction({
     name: "setBackgroundColor",
     available: "frontend", // Executes in the frontend
     description: "Sets the background color of the main page area.",
     parameters: [
       {
         name: "backgroundColor",
         type: "string",
         description: "The background color to set (e.g., 'lightblue', '#FF0000'). Pick visually appealing colors.",
         required: true,
       },
     ],
     // Handler function executed in the browser
     handler: async ({ backgroundColor }) => {
        console.log(`Frontend Action: Setting background to ${backgroundColor}`);
        setBackgroundColor(backgroundColor);
        // Optionally return a string confirmation, though it might not be automatically displayed
        // return `Background color set to ${backgroundColor}`;
     },
      // Optional render function to show status in chat
      render: ({ status, args }) => {
        if (status === "executing") {
          return <div className="text-md italic text-gray-600 p-2">Applying color {args.backgroundColor}...</div>;
        }
        if (status === "complete") {
           return <div className="text-md bg-green-100 text-green-800 p-2 rounded-lg my-2">Background set to {args.backgroundColor}!</div>;
        }
        return <></>; // Return empty fragment otherwise
      }
   });

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

       // Update open item when new events come in for the *latest* state update
       React.useEffect(() => {
         if (timelineEvents.length > 0) {
           setOpenItem(`item-${timelineEvents.length - 1}`);
         } else {
           setOpenItem(undefined);
         }
       }, [timelineEvents.length]); // Only depend on the length changing

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
                            {event.event_type.replace(/_/g, ' ')}
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

  // Main application content area
  return (
    <div
      style={{ backgroundColor, transition: 'background-color 0.5s ease' }}
      className="h-screen w-screen flex justify-center items-center flex-col p-4" // Added padding
    >
      {/* You can add other main content here if needed */}
       <div className="text-center max-w-md p-6 bg-white/80 rounded-lg shadow-lg backdrop-blur-sm">
         <h1 className="text-3xl font-bold mb-4 text-gray-800">Agno + CopilotKit</h1>
         <p className="text-gray-600">
           Interact with the Agno agent using the chat sidebar on the right.
           Watch the agent's timeline update in the chat window! You can also try asking it to change the background color (e.g., "set background to lavender") or greet you.
         </p>
       </div>
    </div>
  );
}