import type { Metadata } from "next";

import { CopilotKit } from "@copilotkit/react-core";

import "@copilotkit/react-ui/styles.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "CoAgents Starter",
  description: "CoAgents Starter",
};

export default function RootLayout({ children }: { children: any }) {
  return (
    <html lang="en">
      <body>
        <CopilotKit
          agent="math_agno_agent" // lock the agent to the sample_agent since we only have one agent
          runtimeUrl="api/copilotkit"
          showDevConsole={true}
        >
          {children}
        </CopilotKit>
      </body>
    </html>
  );
}
