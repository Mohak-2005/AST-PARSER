import axios from 'axios';

const AGENT_BASE = import.meta.env.VITE_AGENT_BASE_URL || 'http://localhost:3001';
const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000';
const REQUEST_TIMEOUT = parseInt(import.meta.env.VITE_REQUEST_TIMEOUT_MS || '3600000', 10);

export const agentApi = axios.create({ baseURL: AGENT_BASE, timeout: REQUEST_TIMEOUT });
export const backendApi = axios.create({ baseURL: API_BASE, timeout: REQUEST_TIMEOUT });

export const startAnalysis = (projectPath) => agentApi.post('/agent/analyze', { projectPath });
export const getAgentStatus = () => agentApi.get('/agent/status');
export const checkAgentHealth = () => agentApi.get('/agent/health');

export const getGraph = (projectId) => backendApi.get(`/api/graph/${projectId}`);
export const getProjects = () => backendApi.get('/api/projects');
export const checkBackendHealth = () => backendApi.get('/api/health');

// Triggers the existing Feature Context Generation service for the current project.
// Accepts codeMapPath string or object with { codeMapPath, projectId }.
export const generateFeatureContext = (params) =>
  backendApi.post(
    '/api/feature-context',
    typeof params === 'string' ? { codeMapPath: params } : params
  );
