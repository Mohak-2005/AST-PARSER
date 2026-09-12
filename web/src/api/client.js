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

// Checks whether valid feature-context files already exist for the current project.
export const checkFeatureContext = (params) =>
  backendApi.get('/api/feature-context', {
    params: typeof params === 'string' ? { codeMapPath: params } : params,
  });

// Triggers the existing Feature Context Generation service for the current project.
// Accepts codeMapPath string or object with { codeMapPath, projectId, force }.
export const generateFeatureContext = (params) =>
  backendApi.post(
    '/api/feature-context',
    typeof params === 'string' ? { codeMapPath: params } : params
  );

// Checks whether valid unit test case files already exist for the current project.
export const checkTestCases = (params) =>
  backendApi.get('/api/test-cases', {
    params: typeof params === 'string' ? { codeMapPath: params } : params,
  });

// Triggers Unit Test Case Generation service for the current project.
export const generateTestCases = (params) =>
  backendApi.post(
    '/api/test-cases',
    typeof params === 'string' ? { codeMapPath: params } : params
  );

// Checks whether valid retrieved code files exist for the current project.
export const checkRetrievedCode = async (params) => {
  const query = typeof params === 'string' ? { codeMapPath: params } : params;
  try {
    const res = await agentApi.get('/agent/retrieved-code', { params: query });
    if (res.data?.status === 'success') return res;
  } catch (e) {
    // Fall back to backend if agent direct call fails
  }
  return backendApi.get('/api/retrieved-code', { params: query });
};

// Triggers Code Retrieval Engine inside the Local Agent.
export const retrieveCode = async (params) => {
  const payload = typeof params === 'string' ? { codeMapPath: params } : params;
  try {
    const res = await agentApi.post('/agent/retrieve-code', payload);
    if (res.data?.status === 'success') return res;
  } catch (e) {
    // Fall back to backend if agent direct call fails
  }
  return backendApi.post('/api/retrieve-code', payload);
};

