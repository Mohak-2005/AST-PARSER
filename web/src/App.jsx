import React, { useState, useCallback, useEffect } from 'react';
import ProjectSelector from './components/ProjectSelector';
import ProjectList from './components/ProjectList';
import ProgressPanel from './components/ProgressPanel';
import GraphView from './components/GraphView';
import NodeDetails from './components/NodeDetails';
import EdgeDetails from './components/EdgeDetails';
import { startAnalysis, getAgentStatus, getGraph, generateFeatureContext } from './api/client';
import { Layers, PlusCircle, Sparkles, CheckCircle2, AlertCircle, Loader2 } from 'lucide-react';

export default function App() {
  const [view, setView] = useState('home'); // 'home' | 'analyzing' | 'graph'
  const [projectId, setProjectId] = useState(null);
  const [graphData, setGraphData] = useState(null);
  const [agentStatus, setAgentStatus] = useState(null);
  const [error, setError] = useState(null);
  
  const [selectedNode, setSelectedNode] = useState(null);
  const [selectedEdge, setSelectedEdge] = useState(null);
  const [graphLookup, setGraphLookup] = useState(null);

  // Feature Context Generation state — idle | loading | success | error
  const [featureCtx, setFeatureCtx] = useState({ status: 'idle', message: null, data: null });

  const handleGenerateFeatureContext = async () => {
    // Prevent duplicate requests
    if (featureCtx.status === 'loading') return;

    // Read codeMapPath from the already-loaded code map (set by agent's codeMapBuilder.js)
    const codeMapPath =
      graphData?.codeMap?.project?.codeMapPath ||
      graphData?.codeMap?.codeMapPath ||
      null;

    if (!codeMapPath && !projectId) {
      setFeatureCtx({
        status: 'error',
        message: 'No code map path available for this project. Re-analyze to enable feature context generation.',
        data: null,
      });
      return;
    }

    setFeatureCtx({ status: 'loading', message: null, data: null });
    try {
      const { data } = await generateFeatureContext({ codeMapPath, projectId });
      setFeatureCtx({ status: 'success', message: data.message, data });
    } catch (err) {
      const errMsg =
        err.response?.data?.detail ||
        err.response?.data?.error ||
        err.message ||
        'Feature context generation failed';
      setFeatureCtx({ status: 'error', message: errMsg, data: null });
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
              {/* Generate Feature Context button */}
              <button
                onClick={handleGenerateFeatureContext}
                disabled={featureCtx.status === 'loading'}
                className="flex items-center gap-2 bg-brand-700 hover:bg-brand-600 disabled:opacity-60 disabled:cursor-not-allowed text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors border border-brand-600"
                title="Generate feature context files from the current project's code map"
              >
                {featureCtx.status === 'loading' ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : featureCtx.status === 'success' ? (
                  <CheckCircle2 className="w-4 h-4" />
                ) : featureCtx.status === 'error' ? (
                  <AlertCircle className="w-4 h-4" />
                ) : (
                  <Sparkles className="w-4 h-4" />
                )}
                {featureCtx.status === 'loading'
                  ? 'Generating Feature Context…'
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

        {/* Feature context status bar — only shown in graph view when there is a message */}
        {view === 'graph' && featureCtx.status !== 'idle' && (
          <div
            className={`text-xs px-2 pb-2 flex items-center gap-2 ${
              featureCtx.status === 'success'
                ? 'text-emerald-400'
                : featureCtx.status === 'error'
                ? 'text-red-400'
                : 'text-slate-400'
            }`}
          >
            {featureCtx.status === 'success' && (
              <>
                <CheckCircle2 className="w-3 h-3 flex-shrink-0" />
                {featureCtx.message}
                {featureCtx.data?.featureCount != null && (
                  <span className="ml-1 font-semibold">
                    ({featureCtx.data.featureCount} feature{featureCtx.data.featureCount !== 1 ? 's' : ''})
                  </span>
                )}
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
                Discovering features and generating context files…
              </>
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
    </div>
  );
}
