'use client';

import { useCoAgent } from "@copilotkit/react-core";
import ReactMarkdown from "react-markdown";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "../components/ui/accordion";

interface TimelineEvent {
  event_type: string;
  event_summary: string;
  event_details: string;
}

export function Timeline() {
  const { state } = useCoAgent({
    name: "math_agno_agent",
    initialState: {
      event_timeline: [{
        event_type: "",
        event_summary: "",
        event_details: ""
      }],
    },
  });

  // Extract timeline events from state
  const events = state?.event_timeline || [];

  return (
    <div className="flex flex-col gap-4 w-full max-w-3xl p-8">
      <h1 className="bg-blue-500 p-10 rounded-xl text-white text-4xl text-center">
        Event Timeline
      </h1>
      
      <div className="bg-white rounded-lg shadow-lg p-6">
        <Accordion type="single" collapsible className="space-y-4">
          {events.map((event: TimelineEvent, index: number) => (
            <AccordionItem key={index} value={`item-${index}`} className="border-l-4 border-blue-500 pl-4 py-2">
              <AccordionTrigger className="hover:no-underline">
                <div className="flex flex-col items-start gap-1">
                  <div className="font-semibold text-lg capitalize">
                    {event.event_type.replace(/_/g, ' ')}
                  </div>
                  <div className="text-gray-600 text-sm">
                    {event.event_summary}
                  </div>
                </div>
              </AccordionTrigger>
              {event.event_details && (
                <AccordionContent>
                  <div className="text-gray-700 bg-gray-50 rounded p-4 prose prose-sm max-w-none">
                    <ReactMarkdown>{event.event_details}</ReactMarkdown>
                  </div>
                </AccordionContent>
              )}
            </AccordionItem>
          ))}
        </Accordion>
        {events.length === 0 && (
          <div className="text-gray-500 text-center py-4">
            No events to display yet
          </div>
        )}
      </div>
    </div>
  );
}