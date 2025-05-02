// ui/app/custom-chat/page.tsx
"use client";

import React, { useState, useMemo, useRef, useEffect, useCallback } from "react";
import { useCopilotChat } from "@copilotkit/react-core";
import { Message, Role, TextMessage } from "@copilotkit/runtime-client-gql";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "../components/ui/accordion"; // Adjust path if needed
import ReactMarkdown from "react-markdown";
import { Send, Square } from "lucide-react"; // Import icons

// --- Type Definitions ---
interface TimelineEvent {
  timestamp: number | string;
  readable_timestamp?: string;
  event_id?: string; // Ensure event_id is present and unique
  event_type: string;
  event_summary: string;
  event_details?: string;
}

interface AgentStatePayload {
  event_timeline?: TimelineEvent[];
  session_state?: Record<string, any>;
}

// --- Unified type for rendering ---
type DisplayItem =
  | { type: "text"; id: string; timestamp: number; message: TextMessage }
  | { type: "timelineEvent"; id: string; timestamp: number; event: TimelineEvent; status: string; nodeName: string | undefined; parentMessageId: string };

// --- Helper Function to Get Timestamp in Milliseconds ---
function getTimestampMs(timestamp: string | number | Date | undefined): number {
  // ... (keep the existing getTimestampMs function) ...
  if (!timestamp) return Date.now();
  try {
    if (typeof timestamp === 'string') { return new Date(timestamp).getTime(); }
    else if (typeof timestamp === 'number') { return timestamp > 1e12 ? timestamp : timestamp * 1000; }
    else if (timestamp instanceof Date) { return timestamp.getTime(); }
  } catch (e) { console.error("Error parsing timestamp:", timestamp, e); }
  return Date.now();
}

// --- Reusable Message Components (Keep UserMessageBubble, AssistantMessageBubble, SingleEventAccordion) ---
// ... (components remain the same) ...
const UserMessageBubble = ({ message }: { message: TextMessage }) => (
    <div className="flex justify-end mb-3">
      <div className="bg-blue-500 text-white rounded-lg py-2 px-4 max-w-[80%] break-words">
        <pre className="whitespace-pre-wrap font-sans">{message.content}</pre>
      </div>
    </div>
  );

const AssistantMessageBubble = ({ message }: { message: TextMessage }) => {
    const isStatusUpdate = /^\s*\[.*?\]/.test(message.content);
    return (
      <div className="flex justify-start mb-3">
        <div className={`rounded-lg py-2 px-4 max-w-[85%] break-words ${
            isStatusUpdate
            ? 'bg-blue-50 border border-blue-200 text-blue-700 text-xs italic'
            : 'bg-gray-200 text-gray-800'
        }`}>
          <ReactMarkdown
            components={{ /* ... markdown components ... */
                p: ({node, ...props}) => <p className="mb-2 last:mb-0" {...props} />,
                ul: ({node, ...props}) => <ul className="list-disc list-inside mb-2 pl-4" {...props} />,
                ol: ({node, ...props}) => <ol className="list-decimal list-inside mb-2 pl-4" {...props} />,
                li: ({node, ...props}) => <li className="mb-1" {...props} />,
                 code: ({node, inline, className, children, ...props}) => {
                  const match = /language-(\w+)/.exec(className || '')
                  return !inline && match ? (
                     <pre className="bg-gray-800 text-white p-2 rounded overflow-x-auto text-sm my-2 font-mono"><code className={className} {...props}>{children}</code></pre>
                  ) : (
                    <code className="bg-gray-300 px-1 rounded text-sm font-mono" {...props}>{children}</code>
                  )
                }
            }}
          >{message.content}</ReactMarkdown>
        </div>
      </div>
    );
};

const SingleEventAccordion = ({ event, status, nodeName }: { event: TimelineEvent, status: string, nodeName: string | undefined }) => {
    const [isOpen, setIsOpen] = useState(true);
    useEffect(() => { setIsOpen(true); }, [event.timestamp, event.event_id]);

    return (
      <div className={`my-1 py-1 px-2 text-xs rounded border ${status === 'inProgress' ? 'border-blue-200 bg-blue-50/50 animate-pulse' : 'border-gray-300 bg-gray-50/50'} transition-colors`}>
        <Accordion type="single" collapsible value={isOpen ? "item-0" : undefined} onValueChange={(val) => setIsOpen(!!val)}>
          <AccordionItem value="item-0" className="border-none">
            <AccordionTrigger className="hover:no-underline p-0 text-xs w-full justify-start font-normal">
              <div className="flex flex-col items-start gap-0 w-full text-left mr-2">
                <div className={`font-medium capitalize-first ${status === 'inProgress' ? 'text-blue-700' : 'text-gray-700'}`}>
                  [{nodeName || 'Step'} - {status}] {event.event_type.replace(/_/g, ' ')}
                </div>
                <div className="text-gray-500 text-xs truncate w-full">
                  {event.event_summary}
                </div>
              </div>
            </AccordionTrigger>
            {event.event_details && (
              <AccordionContent className="px-0 pb-1 pt-1">
                <div className="text-gray-600 bg-white rounded p-2 prose prose-xs max-w-none prose-pre:bg-gray-700 prose-pre:text-gray-200 prose-code:text-indigo-700 border-t mt-1 pt-1">
                  <ReactMarkdown>{event.event_details}</ReactMarkdown>
                </div>
              </AccordionContent>
            )}
          </AccordionItem>
        </Accordion>
      </div>
    );
};
// --- End Reusable Components ---


// --- Main Page Component ---

export default function CustomChatAndDebugPage() {
  const [inputValue, setInputValue] = useState("");
  const { visibleMessages, appendMessage, isLoading, stopGeneration } = useCopilotChat();

  // --- Local State for Chronological Display ---
  const [processedItems, setProcessedItems] = useState<DisplayItem[]>([]);
  const processedIds = useRef<Set<string>>(new Set()); // Track IDs we've already added

  useEffect(() => {
    // Process new messages whenever visibleMessages updates
    const newItemsToAdd: DisplayItem[] = [];

    visibleMessages.forEach((msg: Message) => {
      const messageTimestampMs = getTimestampMs(msg.createdAt);

      if (msg.isTextMessage()) {
        // Check if this text message ID is already processed
        if (!processedIds.current.has(msg.id)) {
          newItemsToAdd.push({
            type: "text",
            id: msg.id, // Use message ID
            timestamp: messageTimestampMs,
            message: msg,
          });
          processedIds.current.add(msg.id); // Mark as processed
        }
      } else if (msg.isAgentStateMessage()) {
        const statePayload = msg.state as AgentStatePayload | null;
        const timeline = statePayload?.event_timeline || [];
        const status = msg.active ? 'inProgress' : 'complete';

        timeline.forEach((event) => {
          const eventId = event.event_id || `${msg.id}-${event.timestamp}`; // Use event_id or fallback

          // Check if this specific timeline event ID is already processed
          if (!processedIds.current.has(eventId)) {
            const eventTsMs = getTimestampMs(event.timestamp) || messageTimestampMs; // Use event timestamp

            newItemsToAdd.push({
              type: "timelineEvent",
              id: eventId, // Use event ID
              timestamp: eventTsMs,
              event: event,
              status: status,
              nodeName: msg.nodeName,
              parentMessageId: msg.id,
            });
            processedIds.current.add(eventId); // Mark as processed
          }
        });
      }
      // TODO: Handle ActionExecutionMessage and ResultMessage similarly if needed
    });

    // If new items were found, add them and re-sort the full list
    if (newItemsToAdd.length > 0) {
      setProcessedItems(prevItems => {
        const combined = [...prevItems, ...newItemsToAdd];
        // Sort primarily by timestamp
        combined.sort((a, b) => a.timestamp - b.timestamp);
        return combined;
      });
    }

    // Note: We don't need to clean up processedIds on unmount unless the component
    // itself remounts frequently without the underlying chat context resetting.
    // If chat context resets (e.g., page reload), processedIds will reset naturally.

  }, [visibleMessages]); // Depend on visibleMessages

  // --- Simplified messages for debug view (using original visibleMessages) ---
  const simplifiedMessagesForDebug = useMemo(() => {
     // ... (keep the existing simplification logic for the debug view) ...
       return visibleMessages.map((msg: Message) => {
       let simplified: Record<string, any> = {
         id: msg.id, type: msg.constructor.name,
         // @ts-ignore
         role: msg.role || undefined, createdAt: msg.createdAt,
       };
       if (msg.isTextMessage()) { simplified.content = msg.content.substring(0, 80) + "..."; }
       else if (msg.isActionExecutionMessage()) { simplified.name = msg.name; simplified.args = JSON.stringify(msg.arguments).substring(0, 80) + "..."; }
       else if (msg.isResultMessage()) { simplified.actionName = msg.actionName; simplified.result = msg.result.substring(0, 80) + "..."; }
       else if (msg.isAgentStateMessage()) {
         simplified.agentName = msg.agentName; simplified.nodeName = msg.nodeName;
         simplified.running = msg.running; simplified.active = msg.active;
         const statePayload = msg.state as AgentStatePayload | null;
         const timeline = statePayload?.event_timeline;
         const lastEvent = timeline && timeline.length > 0 ? timeline[timeline.length - 1] : null;
         simplified.latestEventSummary = lastEvent?.event_summary.substring(0, 50) + "..." || "(no event)";
         simplified.timelineLength = timeline?.length || 0;
       }
       return simplified;
     });
   }, [visibleMessages]);

  // --- Scrolling refs and logic ---
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const chatContainerRef = useRef<HTMLDivElement | null>(null);
  const scrollToBottom = useCallback(() => { messagesEndRef.current?.scrollIntoView({ behavior: "auto" }); },[]);
  // Scroll when the *processed* items list changes length
  useEffect(scrollToBottom, [processedItems.length, scrollToBottom]);

  // --- Form submission ---
  const handleSubmit = (e: React.FormEvent) => { /* ... same as before ... */
      e.preventDefault();
      if (inputValue.trim() && !isLoading) {
        // Reset processed IDs when user sends a new message to start fresh for the new run
        processedIds.current.clear();
        setProcessedItems([]); // Clear displayed items immediately for responsiveness

        appendMessage(new TextMessage({ content: inputValue, role: Role.User }));
        setInputValue("");
      }
   };

  return (
    // --- Main Layout ---
    <div className="flex flex-col md:flex-row h-screen bg-gray-100">
      {/* Left Side: Debug View */}
      <div className="w-full md:w-1/2 lg:w-2/5 h-1/2 md:h-full flex flex-col p-4">
        <h2 className="text-xl font-semibold mb-2 text-gray-700 shrink-0">Debug: `visibleMessages` (Raw Hook Output)</h2>
        <div className="flex-grow bg-gray-900 text-green-400 p-3 rounded-lg shadow-inner overflow-x-auto text-xs font-mono overflow-y-scroll">
          <pre>{JSON.stringify(visibleMessages, null, 2)}</pre>
        </div>
      </div>

      {/* Right Side: Chat Interface */}
      <div className="w-full md:w-1/2 lg:w-3/5 h-1/2 md:h-full flex flex-col bg-white border-l border-gray-300 shadow-lg">
        {/* Header */}
        <header className="p-4 border-b bg-gray-50 shadow-sm sticky top-0 z-10">
          <h1 className="text-xl font-semibold text-gray-800">Custom Chat Interface (Processed)</h1>
        </header>

        {/* Scrollable Message Area - NOW RENDERS processedItems */}
        <div ref={chatContainerRef} className="flex-grow overflow-y-auto p-4 space-y-2">
          {processedItems.map((item, index) => {
            // Key now uses the item's unique ID
            const key = item.id || `${item.type}-${item.timestamp}-${index}`;

            if (item.type === "text") {
              return item.message.role === Role.User ? (
                <UserMessageBubble key={key} message={item.message} />
              ) : (
                <AssistantMessageBubble key={key} message={item.message} />
              );
            } else if (item.type === "timelineEvent") {
              return (
                <div key={key} className="w-full max-w-[85%] mr-auto">
                   <SingleEventAccordion
                       event={item.event}
                       status={item.status}
                       nodeName={item.nodeName}
                   />
                </div>
              );
            }
            return null;
          })}
          <div ref={messagesEndRef} />
        </div>

        {/* Input Area */}
        <div className="p-4 border-t bg-gray-50">
           <form onSubmit={handleSubmit} className="flex items-center gap-2">
            {/* Input and buttons remain the same */}
             <input
              type="text"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              placeholder="Type your message..."
              disabled={isLoading}
              className="flex-grow border rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <button
              type="submit"
              disabled={isLoading || !inputValue.trim()}
              className="bg-blue-600 hover:bg-blue-700 text-white rounded-md p-2 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center justify-center"
              aria-label="Send message"
            >
              <Send size={18} />
            </button>
            {isLoading && (
              <button
                type="button"
                onClick={stopGeneration}
                className="bg-red-500 hover:bg-red-600 text-white rounded-md p-2 transition-colors flex items-center justify-center"
                aria-label="Stop generation"
              >
                <Square size={18} />
              </button>
            )}
          </form>
        </div>
      </div>
    </div>
  );
}