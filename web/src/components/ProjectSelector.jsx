import React, { useState, useEffect } from 'react';
import { checkAgentHealth, checkBackendHealth } from '../api/client';
import { FolderOpen, Zap, AlertCircle, CheckCircle } from 'lucide-react';

export default function ProjectSelector({ onAnalyze, error }) {
  const [path, setPath] = useState('');
  const [loading, setLoading] = useState(false);
  const [agentOk, setAgentOk] = useState(null);
  const [backendOk, setBackendOk] = useState(null);
  
  useEffect(() => {
    const checkStatus = () => {
      checkAgentHealth().then(() => setAgentOk(true)).catch(() => setAgentOk(false));
      checkBackendHealth().then(() => setBackendOk(true)).catch(() => setBackendOk(false));
    };
    checkStatus();
    const interval = setInterval(checkStatus, 3000);
    return () => clearInterval(interval);
  }, []);
  
  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!path.trim()) return;
    setLoading(true);
    await onAnalyze(path.trim());
    setLoading(false);
  };
  
  return (
    <div className="bg-slate-800 rounded-xl border border-slate-700 p-6 shadow-xl">
      <h2 className="text-2xl font-bold mb-2 flex items-center gap-2">
        <FolderOpen className="text-brand-500" />
        Analyze a Project
      </h2>
      <p className="text-slate-400 mb-6">
        Enter the absolute path to a local project directory to build its dependency graph.
      </p>

      {error && (
        <div className="mb-6 p-4 bg-red-900/50 border border-red-500/50 rounded-lg flex items-start gap-3 text-red-200">
          <AlertCircle className="w-5 h-5 mt-0.5 flex-shrink-0" />
          <p>{error}</p>
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1">
            Project Path
          </label>
          <input
            type="text"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="C:\Projects\MyApp"
            className="w-full bg-slate-900 border border-slate-600 rounded-lg px-4 py-3 text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-brand-500"
            disabled={loading}
          />
        </div>
        
        <button
          type="submit"
          disabled={loading || !path.trim() || agentOk === false}
          className="w-full bg-brand-600 hover:bg-brand-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-medium py-3 px-4 rounded-lg flex items-center justify-center gap-2 transition-colors"
        >
          {loading ? (
            <Zap className="w-5 h-5 animate-pulse" />
          ) : (
            <Zap className="w-5 h-5" />
          )}
          {loading ? 'Starting Analysis...' : 'Start Analysis'}
        </button>
      </form>

      <div className="mt-8 pt-6 border-t border-slate-700">
        <h3 className="text-sm font-medium text-slate-400 mb-3">System Status</h3>
        <div className="flex gap-4">
          <div className="flex items-center gap-2">
            {agentOk === null ? (
              <div className="w-4 h-4 rounded-full bg-slate-500 animate-pulse" />
            ) : agentOk ? (
              <CheckCircle className="w-4 h-4 text-emerald-500" />
            ) : (
              <AlertCircle className="w-4 h-4 text-red-500" />
            )}
            <span className="text-sm text-slate-300">Local Agent</span>
          </div>
          <div className="flex items-center gap-2">
            {backendOk === null ? (
              <div className="w-4 h-4 rounded-full bg-slate-500 animate-pulse" />
            ) : backendOk ? (
              <CheckCircle className="w-4 h-4 text-emerald-500" />
            ) : (
              <AlertCircle className="w-4 h-4 text-red-500" />
            )}
            <span className="text-sm text-slate-300">Backend API</span>
          </div>
        </div>
        {agentOk === false && (
          <p className="mt-2 text-xs text-red-400">
            Local Agent is not reachable. Cannot start analysis.
          </p>
        )}
      </div>
    </div>
  );
}
