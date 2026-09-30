import React, { useState, useCallback, useEffect } from 'react';
import ProjectSelector from './components/ProjectSelector';
import ProjectList from './components/ProjectList';
import ProgressPanel from './components/ProgressPanel';
import GraphView from './components/GraphView';
import NodeDetails from './components/NodeDetails';
import EdgeDetails from './components/EdgeDetails';
import RetrievedCodeModal from './components/RetrievedCodeModal';
import TestScriptsModal from './components/TestScriptsModal';
import {
  startAnalysis,
  getAgentStatus,
  getGraph,
  generateFeatureContext,
  checkFeatureContext,
  checkTestCases,
  generateTestCases,
  checkRetrievedCode,
  retrieveCode,
  checkTestScripts,
  generateTestScripts
} from './api/client';
import { Layers, PlusCircle, Sparkles, CheckCircle2, AlertCircle, Loader2, TestTube2, Code2, FileCode } from 'lucide-react';

export default function App() {
  const [view, setView] = useState('home'); // 'home' | 'analyzing' | 'graph'
  const [projectId, setProjectId] = useState(null);
  const [graphData, setGraphData] = useState(null);
  const [agentStatus, setAgentStatus] = useState(null);
  const [error, setError] = useState(null);
  
  const [selectedNode, setSelectedNode] = useState(null);
  const [selectedEdge, setSelectedEdge] = useState(null);
  const [graphLookup, setGraphLookup] = useState(null);

  // Feature Context Generation state — idle | loading | already_exists | success | error
  const [featureCtx, setFeatureCtx] = useState({ status: 'idle', step: 'idle', message: null, data: null });

  // Test Cases Generation state — idle | loading | already_exists | success | error
  const [testCasesState, setTestCasesState] = useState({ status: 'idle', step: 'idle', message: null, data: null });
  const [selectedTestType, setSelectedTestType] = useState('all'); // 'all' | 'unit' | 'functional' | 'api'

  // Code Retrieval state
  const [hasRetrievedCode, setHasRetrievedCode] = useState(false);

  // Retrieved Code inspection modal state
  const [retrievedCodeModal, setRetrievedCodeModal] = useState({
    isOpen: false,
    loading: false,
    files: [],
    error: null
  });

  // Test Scripts state & modal
  const [testScriptsState, setTestScriptsState] = useState({ status: 'idle', message: null });
  const [testScriptsModal, setTestScriptsModal] = useState({
    isOpen: false,
    loading: false,
    scripts: [],
    projectEnvironment: null,
    error: null
  });

  const handleGenerateTestCases = async (forceType = null) => {
    // Prevent duplicate requests while running
    if (testCasesState.status === 'loading') return;

    const codeMapPath =
      graphData?.codeMap?.project?.codeMapPath ||
      graphData?.codeMap?.codeMapPath ||
      null;

    if (!codeMapPath && !projectId) {
      setTestCasesState({
        status: 'error',
        step: 'idle',
        message: 'No code map path available for this project. Re-analyze to enable test case generation.',
        data: null,
      });
      return;
    }

    const testType = forceType || selectedTestType || 'all';
    const payload = { codeMapPath, projectId, testType };
    const typeLabel = testType === 'all' ? 'All (Unit, Functional & API)' : testType.toUpperCase();

    // STEP 1: Check whether test cases already exist FIRST
    setTestCasesState({ status: 'loading', step: 'checking', message: `Checking existing ${typeLabel} test cases…`, data: null });

    try {
      const checkRes = await checkTestCases(payload);
      if (checkRes.data?.alreadyExists) {
        setTestCasesState({
          status: 'already_exists',
          step: 'done',
          message: checkRes.data.message || `${typeLabel} test cases already generated.`,
          data: checkRes.data,
        });
        return;
      }
    } catch (checkErr) {
      console.warn('[TestCaseGenerator] Check notice:', checkErr);
    }

    // STEP 2: Generate test cases via backend
    setTestCasesState({
      status: 'loading',
      step: 'generating',
      message: `Generating ${typeLabel} Test Cases...`,
      data: null,
    });

    try {
      const { data } = await generateTestCases({ ...payload, force: true });
      if (data.alreadyExists) {
        setTestCasesState({
          status: 'already_exists',
          step: 'done',
          message: data.message || `${typeLabel} test cases already generated.`,
          data,
        });
      } else {
        const breakdownStr = data.breakdown ? ` (Unit: ${data.breakdown.unit || 0}, Func: ${data.breakdown.functional || 0}, API: ${data.breakdown.api || 0})` : '';
        setTestCasesState({
          status: 'success',
          step: 'done',
          message: data.message || `Test cases generated successfully (${data.testCaseCount || 0} tests${breakdownStr}).`,
          data,
        });
      }

      // Automatically trigger Local Agent code retrieval in background
      try {
        retrieveCode(payload);
      } catch (e) {
        console.warn('[RetrievedCode] Background retrieval notice:', e);
      }
    } catch (err) {
      const errMsg =
        err.response?.data?.detail ||
        err.response?.data?.error ||
        err.message ||
        'Test case generation failed';
      setTestCasesState({ status: 'error', step: 'idle', message: errMsg, data: null });
    }
  };

  const handleOpenRetrievedCode = async () => {
    const codeMapPath =
      graphData?.codeMap?.project?.codeMapPath ||
      graphData?.codeMap?.codeMapPath ||
      null;

    setRetrievedCodeModal((prev) => ({ ...prev, isOpen: true, loading: true, error: null }));

    try {
      // First check if retrieved code already exists
      const checkRes = await checkRetrievedCode({ codeMapPath, projectId });
      if (checkRes.data?.status === 'success' && checkRes.data.files?.length > 0) {
        setHasRetrievedCode(true);
        setRetrievedCodeModal((prev) => ({
          ...prev,
          isOpen: true,
          loading: false,
          files: checkRes.data.files,
        }));
        return;
      }

      // If not yet retrieved, run retrieval engine
      const retrieveRes = await retrieveCode({ codeMapPath, projectId });
      if (retrieveRes.data?.status === 'success') {
        setHasRetrievedCode(true);
        const filesRes = await checkRetrievedCode({ codeMapPath, projectId });
        setRetrievedCodeModal((prev) => ({
          ...prev,
          isOpen: true,
          loading: false,
          files: filesRes.data?.files || [],
        }));
      } else {
        setRetrievedCodeModal((prev) => ({
          ...prev,
          loading: false,
          error: retrieveRes.data?.message || 'Could not retrieve source code',
        }));
      }
    } catch (err) {
      console.error('[RetrievedCode] Error:', err);
      setRetrievedCodeModal((prev) => ({
        ...prev,
        loading: false,
        error: err.response?.data?.detail || err.message || 'Failed to retrieve code',
      }));
    }
  };

  const handleRefreshRetrievedCode = async () => {
    const codeMapPath =
      graphData?.codeMap?.project?.codeMapPath ||
      graphData?.codeMap?.codeMapPath ||
      null;

    setRetrievedCodeModal((prev) => ({ ...prev, loading: true, error: null }));
    try {
      await retrieveCode({ codeMapPath, projectId });
      setHasRetrievedCode(true);
      const filesRes = await checkRetrievedCode({ codeMapPath, projectId });
      setRetrievedCodeModal((prev) => ({
        ...prev,
        loading: false,
        files: filesRes.data?.files || [],
      }));
    } catch (err) {
      console.error('[RetrievedCode] Refresh error:', err);
      setRetrievedCodeModal((prev) => ({ ...prev, loading: false }));
    }
  };

  const handleGenerateOrOpenTestScripts = async (force = false) => {
    const codeMapPath =
      graphData?.codeMap?.project?.codeMapPath ||
      graphData?.codeMap?.codeMapPath ||
      null;

    setTestScriptsModal((prev) => ({ ...prev, isOpen: true, loading: true, error: null }));
    setTestScriptsState({ status: 'loading', message: 'Generating test scripts...' });

    try {
      if (!force) {
        // First check existing test scripts
        const checkRes = await checkTestScripts({ codeMapPath, projectId });
        if (checkRes.data?.status === 'success' && checkRes.data?.scripts?.length > 0) {
          setTestScriptsModal((prev) => ({
            ...prev,
            isOpen: true,
            loading: false,
            scripts: checkRes.data.scripts,
            projectEnvironment: checkRes.data.projectEnvironment,
          }));
          setTestScriptsState({ status: 'success', message: `Found ${checkRes.data.scripts.length} test script(s).` });
          return;
        }
      }

      // Generate test scripts
      const genRes = await generateTestScripts({ codeMapPath, projectId, force });
      if (genRes.data?.status === 'success') {
        const checkRes = await checkTestScripts({ codeMapPath, projectId });
        setTestScriptsModal((prev) => ({
          ...prev,
          isOpen: true,
          loading: false,
          scripts: checkRes.data?.scripts || genRes.data.scripts || [],
          projectEnvironment: genRes.data.projectEnvironment,
        }));
        setTestScriptsState({ status: 'success', message: genRes.data.message });
      } else {
        setTestScriptsModal((prev) => ({ ...prev, loading: false, error: genRes.data?.message }));
        setTestScriptsState({ status: 'error', message: genRes.data?.message });
      }
    } catch (err) {
      console.error('[TestScripts] Error:', err);
      const errMsg = err.response?.data?.detail || err.message || 'Failed to generate test scripts';
      setTestScriptsModal((prev) => ({ ...prev, loading: false, error: errMsg }));
      setTestScriptsState({ status: 'error', message: errMsg });
    }
  };

  const handleGenerateFeatureContext = async () => {
    // Prevent duplicate requests while running
    if (featureCtx.status === 'loading') return;

    // Read codeMapPath from the already-loaded code map (set by agent's codeMapBuilder.js)
    const codeMapPath =
      graphData?.codeMap?.project?.codeMapPath ||
      graphData?.codeMap?.codeMapPath ||
      null;

    if (!codeMapPath && !projectId) {
      setFeatureCtx({
        status: 'error',
        step: 'idle',
        message: 'No code map path available for this project. Re-analyze to enable feature context generation.',
        data: null,
      });
      return;
    }

    const payload = { codeMapPath, projectId };

    // STEP 1: Check whether feature contexts already exist FIRST
    setFeatureCtx({ status: 'loading', step: 'checking', message: 'Checking existing feature context…', data: null });

    try {
      const checkRes = await checkFeatureContext(payload);
      if (checkRes.data?.alreadyExists) {
        // IF THEY EXIST: Show "Feature context already generated for N features." and STOP. LLM is NOT called.
        setFeatureCtx({
          status: 'already_exists',
          step: 'done',
          message: checkRes.data.message || `Feature context already generated for ${checkRes.data.featureCount} features.`,
          data: checkRes.data,
        });
        return;
      }
    } catch (checkErr) {
      console.warn('[FeatureContext] Check notice:', checkErr);
      // Proceed to generate if check endpoint had temporary issue
    }

    // STEP 2: IF THEY DO NOT EXIST: Show "Generating Feature Context..." and invoke generator
    setFeatureCtx({
      status: 'loading',
      step: 'generating',
      message: 'Generating Feature Context...',
      data: null,
    });

    try {
      const { data } = await generateFeatureContext(payload);
      if (data.alreadyExists) {
        setFeatureCtx({
          status: 'already_exists',
          step: 'done',
          message: data.message || `Feature context already generated for ${data.featureCount} features.`,
          data,
        });
      } else {
        setFeatureCtx({
          status: 'success',
          step: 'done',
          message: data.message || `Feature context generated successfully for ${data.featureCount} features.`,
          data,
        });
      }
    } catch (err) {
      const errMsg =
        err.response?.data?.detail ||
        err.response?.data?.error ||
        err.message ||
        'Feature context generation failed';
      setFeatureCtx({ status: 'error', step: 'idle', message: errMsg, data: null });
    }
  };


  const startPolling = useCallback(() => {
    const interval = setInterval(async () => {
      try {
        const { data } = await getAgentStatus();
        setAgentStatus(data);
        if (data.stage === 'Complete' && data.projectId) {
          clearInterval(interval);
          try {
            const graphRes = await getGraph(data.projectId);
            setGraphData(graphRes.data);
            setProjectId(data.projectId);
            setSelectedNode(null);
            setSelectedEdge(null);
            setView('graph');

            const cmPath = graphRes.data?.codeMap?.project?.codeMapPath || graphRes.data?.codeMap?.codeMapPath || null;
            try {
              const fcRes = await checkFeatureContext({ codeMapPath: cmPath, projectId: data.projectId });
              if (fcRes.data?.alreadyExists) {
                setFeatureCtx({
                  status: 'already_exists',
                  step: 'done',
                  message: fcRes.data.message || `Feature context already generated for ${fcRes.data.featureCount} features.`,
                  data: fcRes.data,
                });
              } else {
                setFeatureCtx({ status: 'idle', step: 'idle', message: null, data: null });
              }
            } catch (e) {
              setFeatureCtx({ status: 'idle', step: 'idle', message: null, data: null });
            }
          } catch(err) {
            setError('Failed to fetch graph data after analysis');
            setView('home');
          }
        } else if (data.stage === 'Error') {
          clearInterval(interval);
          setError(data.error || 'Analysis failed');
          setView('home');
        }
      } catch (e) {
        console.error('Status poll failed', e);
      }
    }, 2000);
    
    // Store interval ID in window so we can clean it up if view changes manually
    window.__analysisInterval = interval;
    
    return () => clearInterval(interval);
  }, []);

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (window.__analysisInterval) {
        clearInterval(window.__analysisInterval);
      }
    };
  }, []);

  const handleAnalyze = async (projectPath) => {
    setError(null);
    setView('analyzing');
    setAgentStatus({ stage: 'Initializing', progress: null, projectId: null, error: null });
    
    if (window.__analysisInterval) clearInterval(window.__analysisInterval);
    
    try {
      await startAnalysis(projectPath);
      startPolling();
    } catch (e) {
      setError(e.response?.data?.detail || e.message || 'Failed to start analysis');
      setView('home');
    }
  };

  const handleSelectProject = async (pid) => {
    setError(null);
    try {
      const { data } = await getGraph(pid);
      setGraphData(data);
      setProjectId(pid);
      setSelectedNode(null);
      setSelectedEdge(null);
      setView('graph');

      // Dynamically detect existing feature-context and test-cases
      const codeMapPath = data?.codeMap?.project?.codeMapPath || data?.codeMap?.codeMapPath || null;
      try {
        const fcRes = await checkFeatureContext({ codeMapPath, projectId: pid });
        if (fcRes.data?.alreadyExists) {
          setFeatureCtx({
            status: 'already_exists',
            step: 'done',
            message: fcRes.data.message || `Feature context already generated for ${fcRes.data.featureCount} features.`,
            data: fcRes.data,
          });
          try {
            const tcRes = await checkTestCases({ codeMapPath, projectId: pid });
            if (tcRes.data?.alreadyExists) {
              setTestCasesState({
                status: 'already_exists',
                step: 'done',
                message: tcRes.data.message || 'Unit test cases already generated.',
                data: tcRes.data,
              });
            } else {
              setTestCasesState({ status: 'idle', step: 'idle', message: null, data: null });
            }
          } catch (e) {
            setTestCasesState({ status: 'idle', step: 'idle', message: null, data: null });
          }
        } else {
          setFeatureCtx({ status: 'idle', step: 'idle', message: null, data: null });
          setTestCasesState({ status: 'idle', step: 'idle', message: null, data: null });
        }
      } catch (e) {
        setFeatureCtx({ status: 'idle', step: 'idle', message: null, data: null });
        setTestCasesState({ status: 'idle', step: 'idle', message: null, data: null });
      }

      // Check existing retrieved code
      try {
        const rcRes = await checkRetrievedCode({ codeMapPath, projectId: pid });
        if (rcRes.data?.status === 'success' && ((rcRes.data.files && rcRes.data.files.length > 0) || (rcRes.data.savedFiles && rcRes.data.savedFiles.length > 0))) {
          setHasRetrievedCode(true);
          try {
            const tsRes = await checkTestScripts({ codeMapPath, projectId: pid });
            if (tsRes.data?.status === 'success' && tsRes.data.scripts?.length > 0) {
              setTestScriptsState({ status: 'success', message: `Found ${tsRes.data.scripts.length} test script(s).` });
            }
          } catch (e) {}
        } else {
          setHasRetrievedCode(false);
        }
      } catch (e) {
        setHasRetrievedCode(false);
      }
    } catch (e) {
      setError('Failed to load project graph');
    }
  };

  const handleNewAnalysis = () => {
    if (window.__analysisInterval) clearInterval(window.__analysisInterval);
    setView('home');
    setGraphData(null);
    setProjectId(null);
    setSelectedNode(null);
    setSelectedEdge(null);
    setGraphLookup(null);
    setFeatureCtx({ status: 'idle', message: null, data: null });
    setTestCasesState({ status: 'idle', message: null, data: null });
    setHasRetrievedCode(false);
    setTestScriptsState({ status: 'idle', message: null });
    setTestScriptsModal({ isOpen: false, loading: false, scripts: [], projectEnvironment: null, error: null });
  };

  const handleNodeClick = (node, lookup) => {
    setSelectedNode(node);
    setSelectedEdge(null);
    if (lookup) setGraphLookup(lookup);
  };

  const handleSelectNodeById = (nodeId) => {
    setSelectedEdge(null);
    if (graphLookup?.fileById?.has(nodeId)) {
      const artifact = graphLookup.fileById.get(nodeId);
      setSelectedNode({
        id: nodeId,
        data: {
          label: artifact.file?.name || nodeId,
          filePath: artifact.file?.relativePath || nodeId,
          language: artifact.file?.language,
          lineCount: artifact.file?.lineCount,
          parseStatus: artifact.parse?.status,
          artifact
        }
      });
    } else {
      setSelectedNode({ id: nodeId, data: { label: nodeId, filePath: nodeId } });
    }
  };

  return (
    <div className="h-screen w-screen flex flex-col bg-slate-900 text-slate-200 overflow-hidden">
      {/* Header */}
      <header className="h-auto flex-shrink-0 bg-slate-950 border-b border-slate-800 px-6 py-0">
        <div className="h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-brand-600 flex items-center justify-center">
              <Layers className="w-5 h-5 text-white" />
            </div>
            <h1 className="text-lg font-bold text-slate-100 tracking-wide">
              AST Dependency Analyzer
            </h1>
          </div>

          {view === 'graph' && (
            <div className="flex items-center gap-3">
              {/* GENERATE TEST SCRIPTS button - visible ONLY after Code Retrieval succeeds, placed immediately to the LEFT of Retrieved Code */}
              {hasRetrievedCode && (
                <button
                  onClick={() => handleGenerateOrOpenTestScripts(false)}
                  disabled={testScriptsState.status === 'loading'}
                  className="flex items-center gap-2 bg-purple-700 hover:bg-purple-600 disabled:opacity-60 disabled:cursor-not-allowed text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors border border-purple-600 shadow-sm"
                  title="Generate or view executable test scripts grounded in source code"
                >
                  {testScriptsState.status === 'loading' ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <FileCode className="w-4 h-4" />
                  )}
                  {testScriptsState.status === 'loading'
                    ? 'Generating Test Scripts...'
                    : testScriptsState.status === 'success'
                    ? 'View Test Scripts'
                    : 'Generate Test Scripts'}
                </button>
              )}

              {/* Retrieved Code button - visible ONLY when test cases have been generated, placed immediately to the LEFT of Generated Test Cases */}
              {(testCasesState.status === 'success' || testCasesState.status === 'already_exists') && (
                <button
                  onClick={handleOpenRetrievedCode}
                  className="flex items-center gap-2 bg-indigo-700 hover:bg-indigo-600 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors border border-indigo-600 shadow-sm"
                  title="Inspect retrieved project source code for generated unit test cases"
                >
                  <Code2 className="w-4 h-4" />
                  Retrieved Code
                </button>
              )}

              {/* Generate Test Cases button & Test Type Selector - visible ONLY when feature-context generation has successfully completed */}
              {(featureCtx.status === 'success' || featureCtx.status === 'already_exists') && (
                <div className="flex items-center rounded-lg bg-slate-800/90 border border-slate-700/80 p-0.5 shadow-sm">
                  <select
                    value={selectedTestType}
                    onChange={(e) => setSelectedTestType(e.target.value)}
                    disabled={testCasesState.status === 'loading'}
                    className="bg-transparent text-xs font-semibold text-emerald-400 pl-2 pr-1 py-1.5 focus:outline-none cursor-pointer disabled:opacity-50 border-r border-slate-700 mr-1"
                    title="Select Test Type to generate"
                  >
                    <option value="all" className="bg-slate-900 text-slate-200">All (Unit+Func+API)</option>
                    <option value="unit" className="bg-slate-900 text-slate-200">Unit Tests</option>
                    <option value="functional" className="bg-slate-900 text-slate-200">Functional Tests</option>
                    <option value="api" className="bg-slate-900 text-slate-200">API Tests</option>
                  </select>

                  <button
                    onClick={() => handleGenerateTestCases()}
                    disabled={testCasesState.status === 'loading'}
                    className="flex items-center gap-1.5 bg-emerald-700 hover:bg-emerald-600 disabled:opacity-60 disabled:cursor-not-allowed text-white px-3 py-1.5 rounded-md text-xs font-medium transition-colors"
                    title={`Generate ${selectedTestType} test case specifications from the current project's code map`}
                  >
                    {testCasesState.status === 'loading' ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : testCasesState.status === 'already_exists' || testCasesState.status === 'success' ? (
                      <CheckCircle2 className="w-3.5 h-3.5 text-emerald-300" />
                    ) : testCasesState.status === 'error' ? (
                      <AlertCircle className="w-3.5 h-3.5" />
                    ) : (
                      <TestTube2 className="w-3.5 h-3.5" />
                    )}
                    {testCasesState.status === 'loading'
                      ? 'Generating...'
                      : testCasesState.status === 'already_exists'
                      ? 'Tests Generated'
                      : testCasesState.status === 'success'
                      ? 'Tests Generated'
                      : 'Generate Tests'}
                  </button>
                </div>
              )}

              {/* Generate Feature Context button */}
              <button
                onClick={handleGenerateFeatureContext}
                disabled={featureCtx.status === 'loading'}
                className="flex items-center gap-2 bg-brand-700 hover:bg-brand-600 disabled:opacity-60 disabled:cursor-not-allowed text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors border border-brand-600"
                title="Generate feature context files from the current project's code map"
              >
                {featureCtx.status === 'loading' ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : featureCtx.status === 'already_exists' || featureCtx.status === 'success' ? (
                  <CheckCircle2 className="w-4 h-4 text-emerald-300" />
                ) : featureCtx.status === 'error' ? (
                  <AlertCircle className="w-4 h-4" />
                ) : (
                  <Sparkles className="w-4 h-4" />
                )}
                {featureCtx.status === 'loading'
                  ? 'Generating Feature Context...'
                  : featureCtx.status === 'already_exists'
                  ? 'Feature Context Already Generated'
                  : featureCtx.status === 'success'
                  ? 'Feature Context Generated'
                  : 'Generate Feature Context'}
              </button>

              {/* New Analysis button */}
              <button
                onClick={handleNewAnalysis}
                className="flex items-center gap-2 bg-slate-800 hover:bg-slate-700 text-slate-300 px-4 py-2 rounded-lg text-sm font-medium transition-colors border border-slate-700"
              >
                <PlusCircle className="w-4 h-4" />
                New Analysis
              </button>
            </div>
          )}
        </div>

        {/* Status bar — shown in graph view when there is a feature-context or test-case message */}
        {view === 'graph' && (featureCtx.status !== 'idle' || testCasesState.status !== 'idle') && (
          <div className="flex flex-col gap-1 px-2 pb-2">
            {featureCtx.status !== 'idle' && (
              <div
                className={`text-xs flex items-center gap-2 ${
                  featureCtx.status === 'success' || featureCtx.status === 'already_exists'
                    ? 'text-emerald-400'
                    : featureCtx.status === 'error'
                    ? 'text-red-400'
                    : 'text-slate-400'
                }`}
              >
                {(featureCtx.status === 'success' || featureCtx.status === 'already_exists') && (
                  <>
                    <CheckCircle2 className="w-3 h-3 flex-shrink-0" />
                    {featureCtx.message}
                  </>
                )}
                {featureCtx.status === 'error' && (
                  <>
                    <AlertCircle className="w-3 h-3 flex-shrink-0" />
                    {featureCtx.message}
                  </>
                )}
                {featureCtx.status === 'loading' && (
                  <>
                    <Loader2 className="w-3 h-3 animate-spin flex-shrink-0" />
                    {featureCtx.message || 'Generating Feature Context...'}
                  </>
                )}
              </div>
            )}
            {testCasesState.status !== 'idle' && (
              <div
                className={`text-xs flex items-center gap-2 ${
                  testCasesState.status === 'success' || testCasesState.status === 'already_exists'
                    ? 'text-emerald-400'
                    : testCasesState.status === 'error'
                    ? 'text-red-400'
                    : 'text-slate-400'
                }`}
              >
                {(testCasesState.status === 'success' || testCasesState.status === 'already_exists') && (
                  <>
                    <CheckCircle2 className="w-3 h-3 flex-shrink-0" />
                    {testCasesState.message}
                  </>
                )}
                {testCasesState.status === 'error' && (
                  <>
                    <AlertCircle className="w-3 h-3 flex-shrink-0" />
                    {testCasesState.message}
                  </>
                )}
                {testCasesState.status === 'loading' && (
                  <>
                    <Loader2 className="w-3 h-3 animate-spin flex-shrink-0" />
                    {testCasesState.message || 'Generating Unit Test Cases...'}
                  </>
                )}
              </div>
            )}
          </div>
        )}
      </header>


      {/* Main Content */}
      <main className="flex-grow relative overflow-hidden">
        {view === 'home' && (
          <div className="h-full max-w-6xl mx-auto p-8 grid grid-cols-1 md:grid-cols-2 gap-8">
            <div className="flex flex-col">
              <ProjectSelector onAnalyze={handleAnalyze} error={error} />
            </div>
            <div className="flex flex-col h-[500px]">
              <ProjectList onSelect={handleSelectProject} />
            </div>
          </div>
        )}

        {view === 'analyzing' && (
          <div className="h-full p-8 overflow-y-auto">
            <ProgressPanel status={agentStatus} />
          </div>
        )}

        {view === 'graph' && graphData && (
          <div className="h-full flex w-full">
            <div className="flex-grow h-full relative">
              <GraphView 
                graphData={graphData} 
                selectedNodeId={selectedNode?.id}
                selectedEdgeId={selectedEdge?.id}
                onNodeClick={handleNodeClick}
                onEdgeClick={(edge) => { setSelectedEdge(edge); setSelectedNode(null); }}
              />
            </div>
            
            {(selectedNode || selectedEdge) && (
              <div className="w-[420px] flex-shrink-0 h-full shadow-2xl relative z-20">
                {selectedNode && (
                  <NodeDetails 
                    node={selectedNode} 
                    lookup={graphLookup}
                    onSelectNode={handleSelectNodeById}
                    onClose={() => setSelectedNode(null)} 
                  />
                )}
                {selectedEdge && (
                  <EdgeDetails edge={selectedEdge} onClose={() => setSelectedEdge(null)} />
                )}
              </div>
            )}
          </div>
        )}
      </main>

      {/* Retrieved Code Inspection Modal */}
      <RetrievedCodeModal
        isOpen={retrievedCodeModal.isOpen}
        onClose={() => setRetrievedCodeModal((prev) => ({ ...prev, isOpen: false }))}
        files={retrievedCodeModal.files}
        loading={retrievedCodeModal.loading}
        onRefresh={handleRefreshRetrievedCode}
        projectName={graphData?.projectName || ''}
      />

      {/* Generated Test Scripts Modal */}
      <TestScriptsModal
        isOpen={testScriptsModal.isOpen}
        onClose={() => setTestScriptsModal((prev) => ({ ...prev, isOpen: false }))}
        scripts={testScriptsModal.scripts}
        projectEnv={testScriptsModal.projectEnvironment}
        loading={testScriptsModal.loading}
        onRefresh={() => handleGenerateOrOpenTestScripts(true)}
        projectName={graphData?.projectName || ''}
        codeMapPath={graphData?.codeMap?.project?.codeMapPath || graphData?.codeMap?.codeMapPath || null}
        projectId={projectId}
      />
    </div>
  );
}
