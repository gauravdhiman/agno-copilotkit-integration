"use client";

import { useCopilotAction } from "@copilotkit/react-core";
import { CopilotSidebar } from "@copilotkit/react-ui";
import { useState } from "react";
import { Timeline } from "./components/Timeline";

export default function Home() {
  return (
    <main>
      <YourMainContent />
      <CopilotSidebar
        defaultOpen={true}
        labels={{
          title: "Popup Assistant",
          initial: "Hi! I'm connected to an agent. How can I help?",
        }}
      />
    </main>
  );
}

function YourMainContent() {
  const [backgroundColor, setBackgroundColor] = useState("#ADD8E6");

  // Render a greeting in the chat
  useCopilotAction({
    name: "greetUser",
    available: "remote", // make this available only to the agent
    parameters: [
      {
        name: "name",
        description: "The name of the user to greet.",
      },
    ],
    render: ({ args }) => {
      return (
        <div className="text-lg font-bold bg-blue-500 text-white p-2 rounded-xl text-center">
          Hello, {args.name}!
        </div>
      );
    },
  });

  // Action for setting the background color
  useCopilotAction({
    name: "setBackgroundColor",
    available: "remote", // make this available only to the agent
    parameters: [
      {
        name: "backgroundColor",
        description:
          "The background color to set. Make sure to pick nice colors.",
      },
    ],
    handler({ backgroundColor }) {
      setBackgroundColor(backgroundColor);
    },
  });

  return (
    <div
      style={{ backgroundColor }}
      className="h-screen w-screen flex justify-center items-center flex-col"
    >
      <Timeline />
    </div>
  );
}
