# main.py
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from copilotkit import CopilotKitRemoteEndpoint
from copilotkit.integrations.fastapi import add_fastapi_endpoint

# Import your Agno Agents/Workflows
from sample_agent import sample_agent
# from agno_agents.my_agno_workflow import my_workflow_instance

# Import your adapters (adjust path)
from copilotkit_integration.agno_agent_adapter import AgnoAgentAdapter
from copilotkit_integration.agno_workflow_adapter import AgnoWorkflowAdapter

# --- Create CopilotKit SDK Endpoint ---
sdk = CopilotKitRemoteEndpoint(
    agents=[
        AgnoAgentAdapter(agno_agent_instance=sample_agent),
        # AgnoWorkflowAdapter(agno_workflow_instance=my_actual_agno_workflow),
    ],
)

# --- Setup FastAPI App ---
app = FastAPI()

# Configure CORS (same as before)
origins = [
    "http://localhost:3000",  # Default Next.js port
    "http://localhost:4000",
    "http://127.0.0.1:4000",
    "http://localhost:8000",
    "http://localhost:8080",
    # Add your production frontend URL here if applicable
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=86400,  # Cache preflight requests for 24 hours
)

add_fastapi_endpoint(
    fastapi_app=app,
    sdk=sdk,
    prefix="/api/copilotkit",
)

# --- Add other FastAPI routes ---
@app.get("/")
def read_root():
    return {"Hello": "World"}

# --- Run the app ---
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("AGNO_BACKEND_PORT", 8080))
    print(f"Starting Uvicorn server on 0.0.0.0:{port}")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True) # Use reload for development