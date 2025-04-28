// ui/app/components/Timeline.tsx
'use client';

import React from "react";
import { useCoAgent } from "@copilotkit/react-core";
import ReactMarkdown from "react-markdown";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "../components/ui/accordion"; // Ensure correct path
import { useState, useEffect } from "react";

// Define the structure based on backend's TimelineEvent Pydantic model
interface TimelineEvent {
  timestamp: string; // Added timestamp
  event_type: string;
  event_summary: string;
  event_details?: string; // Marked as optional
}

// Define the structure for the agent's state, focusing on the timeline
interface AgentState {
  event_timeline?: TimelineEvent[];
  // Include other state properties if needed for display, e.g., session_state
  session_state?: Record<string, any>;
}

export function Timeline() {
  // Use the defined AgentState interface
  const { state, name: agentName } = useCoAgent<AgentState>({
    name: "math_agno_agent", // Make sure this matches the backend adapter name
    initialState: { // Provide a default structure matching AgentState
      event_timeline: [],
      session_state: {},
    },
  });

  // Memoize events to prevent unnecessary re-renders if state object reference changes but timeline content doesn't
  const events = React.useMemo(() => state?.event_timeline || [], [state?.event_timeline]);

  // State to manage open accordion item
  const [openItem, setOpenItem] = useState<string | undefined>(
    events.length > 0 ? `item-${events.length - 1}` : undefined
  );

  // Effect to automatically open the latest event when the timeline updates
  useEffect(() => {
    if (events.length > 0) {
      setOpenItem(`item-${events.length - 1}`);
    }
  }, [events.length]); // Depend only on the number of events

  return (
    <div className="flex flex-col gap-4 w-full max-w-3xl p-4 md:p-8"> {/* Responsive padding */}
      <h1 className="bg-gradient-to-r from-blue-500 to-purple-600 p-4 md:p-6 rounded-xl text-white text-2xl md:text-3xl font-bold text-center shadow-md"> {/* Enhanced styling */}
        {agentName ? `${agentName.replace(/_/g, ' ')} Timeline` : "Event Timeline"} {/* Dynamic title */}
      </h1>

      <div className="bg-white rounded-lg shadow-lg p-4 md:p-6 overflow-hidden"> {/* Prevent overflow issues */}
        {/* Use value and onValueChange for controlled accordion */}
        <Accordion type="single" collapsible value={openItem} onValueChange={setOpenItem} className="space-y-3">
          {events.map((event, index) => (
            <AccordionItem key={event.timestamp || index} value={`item-${index}`} className="border rounded-md overflow-hidden transition-shadow hover:shadow-sm">
              <AccordionTrigger className="hover:no-underline px-4 py-3 bg-gray-50 hover:bg-gray-100 transition-colors w-full text-left">
                <div className="flex flex-col items-start gap-1 w-full">
                  <div className="font-semibold text-base md:text-lg text-gray-800 capitalize-first"> {/* Use custom utility */}
                    {event.event_type.replace(/_/g, ' ')}
                  </div>
                  <div className="text-gray-600 text-xs md:text-sm truncate w-full pr-8"> {/* Allow truncation */}
                    {event.event_summary}
                  </div>
                </div>
              </AccordionTrigger>
              {/* Render content only if details exist */}
              {event.event_details && (
                <AccordionContent className="p-4 border-t border-gray-200">
                  <div className="text-gray-700 bg-gray-50 rounded p-3 prose prose-sm max-w-none prose-pre:bg-gray-800 prose-pre:text-gray-200 prose-code:text-purple-700"> {/* Improved prose styling */}
                    <ReactMarkdown>{event.event_details}</ReactMarkdown>
                  </div>
                </AccordionContent>
              )}
            </AccordionItem>
          ))}
        </Accordion>
        {events.length === 0 && (
          <div className="text-gray-500 text-center py-6 italic">
            No agent events yet. Start a conversation!
          </div>
        )}
      </div>
    </div>
  );
}