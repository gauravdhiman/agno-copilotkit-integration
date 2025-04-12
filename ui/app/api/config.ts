// API Configuration
export const backendApiBaseUrl = process.env.NEXT_PUBLIC_BACKEND_API_BASE_URL || 'http://localhost:8080/api';

// API Endpoints
export const API_ENDPOINTS = {
    agentStop: `${backendApiBaseUrl}/agent/stop`,
    agentHumanInput: `${backendApiBaseUrl}/agent/human_input`,
    copilotkit: `${backendApiBaseUrl}/copilotkit`
};