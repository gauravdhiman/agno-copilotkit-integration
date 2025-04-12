# Purpose of this project
This is a small project where I try to integrate [Copilotkit](https://www.copilotkit.ai/) project with [Agno](https://www.agno.com/) framework. I want copilitkit to work with Agno, as it works with CrewAI and Langraph, so that developers can enable applications with Agno based ai agents.

# Project Structure

## Backend
The backend is implemented in Python and consists of the following key components:
- `backend/copilotkit_integration/`: Contains the core integration logic between Copilotkit and Agno
  - `agno_agent_adapter.py`: Adapter for Agno agents
  - `agno_workflow_adapter.py`: Workflow integration with Agno
  - `utils.py`: Utility functions for the integration
- `backend/main.py`: Main application entry point
- `backend/sample_agent.py`: Example implementation of an Agno agent

## Frontend (UI)
The frontend is built using modern web technologies:
- Framework: Next.js with TypeScript
- Styling: Tailwind CSS
- Key files:
  - `ui/app/`: Contains the Next.js application pages and components
  - `ui/public/`: Static assets
  - `ui/components.json`: UI component configurations

# Installation and Setup

## Backend Setup
1. Navigate to the backend directory:
   ```bash
   cd backend
   ```

2. Install Python dependencies:
   ```bash
   uv pip install -r requirements.txt
   ```

3. Start the backend server:
   ```bash
   uvicorn main:app --host 0.0.0.0 --port 8000 --reload
   ```
   The backend will be available at http://localhost:8000

## Frontend Setup
1. Navigate to the UI directory:
   ```bash
   cd ui
   ```

2. Install frontend dependencies:
   ```bash
   pnpm install
   ```

3. Start the development server:
   ```bash
   pnpm run dev
   ```
   The frontend will be available at http://localhost:3000

# License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

# Contributing
Contributions are welcome! Here's how you can help:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

