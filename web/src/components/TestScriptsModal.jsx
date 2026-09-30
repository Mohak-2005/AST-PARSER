import React, { useState, useEffect, useMemo } from 'react';
import {
  X,
  FileCode,
  Copy,
  Check,
  RefreshCw,
  AlertCircle,
  CheckCircle2,
  Layers,
  Search,
  Terminal,
  FolderTree,
  Play,
  Loader2,
  Clock
} from 'lucide-react';
import { executeTests, checkTestExecution } from '../api/client';

export default function TestScriptsModal({
  isOpen,
  onClose,
  scripts = [],
  projectEnv = null,
  loading = false,
  onRefresh,
  projectName = '',
  codeMapPath = null,
  projectId = ''
}) {
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [copied, setCopied] = useState(false);
  const [filterText, setFilterText] = useState('');
  const [selectedType, setSelectedType] = useState('all'); // 'all' | 'unit' | 'functional' | 'api'
  const [activeTab, setActiveTab] = useState('code'); // 'code' | 'execution'
  const [executing, setExecuting] = useState(false);
  const [executionResult, setExecutionResult] = useState(null);
  const [executionError, setExecutionError] = useState(null);

  // Reset selection when scripts change
  useEffect(() => {
    setSelectedIdx(0);
  }, [scripts, selectedType]);

  // Load existing execution summary if available on open
  useEffect(() => {
    if (isOpen && (codeMapPath || projectId)) {
      checkTestExecution({ codeMapPath, projectId })
        .then((res) => {
          if (res.data && res.data.status === 'success' && res.data.results?.length > 0) {
            setExecutionResult(res.data);
          }
        })
        .catch(() => {});
    }
  }, [isOpen, codeMapPath, projectId]);

  // Filtered scripts list by testType and search text
  const filteredScripts = useMemo(() => {
    return scripts.filter((s) => {
      const sType = (s.testType || (s.fileName && s.fileName.toLowerCase().includes('api') ? 'api' : 'unit')).toLowerCase();
      if (selectedType !== 'all') {
        if (selectedType === 'functional' && !['functional', 'e2e'].includes(sType)) return false;
        if (selectedType === 'api' && sType !== 'api') return false;
        if (selectedType === 'unit' && sType !== 'unit') return false;
      }
      if (!filterText.trim()) return true;
      const q = filterText.toLowerCase();
      return (
        (s.testCaseId && s.testCaseId.toLowerCase().includes(q)) ||
        (s.testCaseTitle && s.testCaseTitle.toLowerCase().includes(q)) ||
        (s.target && s.target.toLowerCase().includes(q)) ||
        (s.fileName && s.fileName.toLowerCase().includes(q)) ||
        (s.group && s.group.toLowerCase().includes(q)) ||
        (s.content && s.content.toLowerCase().includes(q))
      );
    });
  }, [scripts, selectedType, filterText]);

  // Count by test type
  const typeCounts = useMemo(() => {
    const counts = { all: scripts.length, unit: 0, functional: 0, api: 0 };
    scripts.forEach((s) => {
      const st = (s.testType || (s.fileName && s.fileName.toLowerCase().includes('api') ? 'api' : 'unit')).toLowerCase();
      if (st === 'api') counts.api++;
      else if (st === 'functional' || st === 'e2e') counts.functional++;
      else counts.unit++;
    });
    return counts;
  }, [scripts]);

  const activeScript = filteredScripts[selectedIdx] || filteredScripts[0] || null;

  // Handle copy script
  const handleCopyCode = (code) => {
    if (!code) return;
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // Handle test execution
  const handleRunTests = async () => {
    setExecuting(true);
    setExecutionError(null);
    setActiveTab('execution');
    try {
      const res = await executeTests({
        codeMapPath,
        projectId,
        testType: selectedType === 'all' ? null : selectedType
      });
      setExecutionResult(res.data);
    } catch (err) {
      setExecutionError(err.response?.data?.detail || err.message || 'Failed to execute test scripts');
    } finally {
      setExecuting(false);
    }
  };

  if (!isOpen) return null;

  const envLang = projectEnv?.language || 'auto-detected';
  const envFramework = projectEnv?.test_framework || 'standard';

  const getTestTypeBadge = (type) => {
    const norm = (type || 'unit').toLowerCase();
    if (norm === 'api') {
      return <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 font-mono border border-emerald-500/40">API</span>;
    }
    if (norm === 'functional' || norm === 'e2e') {
      return <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-300 font-mono border border-purple-500/40">FUNC</span>;
    }
    return <span className="text-[10px] px-1.5 py-0.5 rounded bg-sky-500/20 text-sky-300 font-mono border border-sky-500/40">UNIT</span>;
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="bg-slate-900 border border-slate-700/80 rounded-xl shadow-2xl w-full max-w-6xl h-[88vh] flex flex-col overflow-hidden">
        {/* Header */}
        <div className="px-6 py-3.5 bg-slate-800/80 border-b border-slate-700/80 flex items-center justify-between flex-shrink-0">
          <div className="flex items-center space-x-3">
            <div className="p-2 bg-indigo-500/20 text-indigo-400 rounded-lg border border-indigo-500/30">
              <FileCode className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <h2 className="text-lg font-bold text-white tracking-wide">
                  Generated Test Scripts
                </h2>
                {projectName && (
                  <span className="text-xs px-2 py-0.5 rounded bg-slate-700 text-slate-300 font-mono">
                    {projectName}
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-400 mt-0.5">
                Dynamic 1:1 test scripts for Unit, Functional, and API verification
              </p>
            </div>
          </div>

          <div className="flex items-center space-x-3">
            {projectEnv && (
              <span className="hidden sm:inline-flex items-center space-x-1 px-2.5 py-1 rounded-full text-xs font-mono bg-indigo-950/60 text-indigo-300 border border-indigo-700/50">
                <Terminal className="w-3.5 h-3.5 mr-1 text-indigo-400" />
                <span>{envLang.toUpperCase()}</span>
                <span>•</span>
                <span>{envFramework.toUpperCase()}</span>
              </span>
            )}

            {/* Run Tests Button */}
            <button
              onClick={handleRunTests}
              disabled={executing || scripts.length === 0}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white transition-colors border border-emerald-500 shadow-sm"
              title="Execute test scripts using project's detected test runner"
            >
              {executing ? (
                <>
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  <span>Running...</span>
                </>
              ) : (
                <>
                  <Play className="w-3.5 h-3.5 fill-current" />
                  <span>Run Tests</span>
                </>
              )}
            </button>

            {onRefresh && (
              <button
                onClick={onRefresh}
                disabled={loading}
                className="p-1.5 text-slate-400 hover:text-white rounded-lg hover:bg-slate-700/50 transition-colors"
                title="Regenerate Test Scripts"
              >
                <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin text-indigo-400' : ''}`} />
              </button>
            )}
            <button
              onClick={onClose}
              className="p-1.5 text-slate-400 hover:text-white rounded-lg hover:bg-slate-700/50 transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Content Body */}
        <div className="flex-1 flex overflow-hidden">
          {/* Left Sidebar: Filters and Script List */}
          <div className="w-80 border-r border-slate-800 flex flex-col bg-slate-900/50 flex-shrink-0">
            {/* Test Type Tabs */}
            <div className="flex border-b border-slate-800 p-1.5 gap-1 bg-slate-950/60 text-xs font-medium">
              {[
                { id: 'all', label: 'All', count: typeCounts.all },
                { id: 'unit', label: 'Unit', count: typeCounts.unit },
                { id: 'functional', label: 'Func', count: typeCounts.functional },
                { id: 'api', label: 'API', count: typeCounts.api },
              ].map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setSelectedType(tab.id)}
                  className={`flex-1 py-1 px-1.5 rounded text-center transition-colors flex items-center justify-center gap-1 ${
                    selectedType === tab.id
                      ? 'bg-indigo-600 text-white font-semibold shadow-sm'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800'
                  }`}
                >
                  <span>{tab.label}</span>
                  <span className={`text-[10px] px-1 rounded-full ${selectedType === tab.id ? 'bg-indigo-700' : 'bg-slate-800 text-slate-400'}`}>
                    {tab.count}
                  </span>
                </button>
              ))}
            </div>

            {/* Search box */}
            <div className="p-2.5 border-b border-slate-800">
              <div className="relative">
                <Search className="w-4 h-4 absolute left-3 top-2.5 text-slate-400" />
                <input
                  type="text"
                  placeholder="Filter scripts..."
                  value={filterText}
                  onChange={(e) => {
                    setFilterText(e.target.value);
                    setSelectedIdx(0);
                  }}
                  className="w-full pl-9 pr-3 py-1.5 text-xs bg-slate-800 border border-slate-700 rounded-lg text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
                />
              </div>
            </div>

            {/* List */}
            <div className="flex-1 overflow-y-auto p-2 space-y-1">
              {filteredScripts.length === 0 ? (
                <div className="text-center py-8 text-xs text-slate-500">
                  No {selectedType !== 'all' ? selectedType : ''} test scripts found.
                </div>
              ) : (
                filteredScripts.map((s, idx) => {
                  const isSelected = idx === selectedIdx;
                  const sType = s.testType || (s.fileName && s.fileName.toLowerCase().includes('api') ? 'api' : 'unit');
                  return (
                    <button
                      key={s.filePath || idx}
                      onClick={() => {
                        setSelectedIdx(idx);
                        setActiveTab('code');
                      }}
                      className={`w-full text-left p-2.5 rounded-lg text-xs transition-colors flex flex-col space-y-1 ${
                        isSelected && activeTab === 'code'
                          ? 'bg-indigo-600/20 text-indigo-200 border border-indigo-500/40'
                          : 'hover:bg-slate-800/60 text-slate-300 border border-transparent'
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center space-x-1.5 font-medium truncate">
                          <FileCode className="w-3.5 h-3.5 text-indigo-400 flex-shrink-0" />
                          <span className="font-mono text-slate-200 truncate" title={s.fileName}>
                            {s.fileName || `${s.testCaseId}.code`}
                          </span>
                        </div>
                        <div className="flex items-center space-x-1 flex-shrink-0 ml-1">
                          {getTestTypeBadge(sType)}
                          {s.status === 'success' ? (
                            <span className="w-2 h-2 rounded-full bg-emerald-400" title="Cross-validation passed" />
                          ) : (
                            <span className="w-2 h-2 rounded-full bg-red-400 animate-pulse" title={`Validation failed: ${s.failureType || 'INVALID'}`} />
                          )}
                        </div>
                      </div>
                      {s.testCaseTitle && s.testCaseTitle !== s.fileName && (
                        <div className="text-[11px] text-slate-300 truncate" title={s.testCaseTitle}>
                          {s.testCaseTitle}
                        </div>
                      )}
                      {s.group && (
                        <div className="text-[10px] text-slate-400 truncate flex items-center space-x-1">
                          <FolderTree className="w-3 h-3 text-slate-500" />
                          <span>{s.group}</span>
                        </div>
                      )}
                    </button>
                  );
                })
              )}
            </div>

            {/* Bottom summary count */}
            <div className="p-3 border-t border-slate-800 text-[11px] text-slate-400 flex justify-between items-center bg-slate-900/80">
              <span>Total: <span className="font-mono font-medium text-slate-200">{filteredScripts.length}</span></span>
              <span className="flex items-center space-x-1.5 text-[10px] font-mono">
                <span className="text-emerald-400">
                  {filteredScripts.filter((s) => s.status === 'success').length} valid
                </span>
                {filteredScripts.some((s) => s.status !== 'success') && (
                  <span className="text-red-400">
                    • {filteredScripts.filter((s) => s.status !== 'success').length} failed
                  </span>
                )}
              </span>
            </div>
          </div>

          {/* Right Main Pane */}
          <div className="flex-1 flex flex-col overflow-hidden bg-slate-950">
            {/* View Mode Tabs (Script Code vs Execution Output) */}
            <div className="flex items-center justify-between border-b border-slate-800 px-4 bg-slate-900/40">
              <div className="flex gap-2">
                <button
                  onClick={() => setActiveTab('code')}
                  className={`py-2 px-3 text-xs font-medium border-b-2 transition-colors flex items-center gap-1.5 ${
                    activeTab === 'code'
                      ? 'border-indigo-500 text-indigo-300 font-semibold'
                      : 'border-transparent text-slate-400 hover:text-slate-200'
                  }`}
                >
                  <FileCode className="w-3.5 h-3.5" />
                  <span>Script Code</span>
                </button>
                <button
                  onClick={() => setActiveTab('execution')}
                  className={`py-2 px-3 text-xs font-medium border-b-2 transition-colors flex items-center gap-1.5 ${
                    activeTab === 'execution'
                      ? 'border-indigo-500 text-indigo-300 font-semibold'
                      : 'border-transparent text-slate-400 hover:text-slate-200'
                  }`}
                >
                  <Terminal className="w-3.5 h-3.5" />
                  <span>Execution Output</span>
                  {executionResult && (
                    <span className={`text-[10px] px-1.5 py-0.2 rounded-full font-mono ${
                      executionResult.failed > 0 ? 'bg-red-900/60 text-red-300' : 'bg-emerald-900/60 text-emerald-300'
                    }`}>
                      {executionResult.passed}/{executionResult.totalTests}
                    </span>
                  )}
                </button>
              </div>

              {activeTab === 'code' && activeScript && (
                <button
                  onClick={() => handleCopyCode(activeScript.content || activeScript.script)}
                  className="px-2.5 py-1 rounded text-xs font-medium bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 flex items-center space-x-1.5 transition-colors"
                >
                  {copied ? (
                    <>
                      <Check className="w-3 h-3 text-emerald-400" />
                      <span>Copied</span>
                    </>
                  ) : (
                    <>
                      <Copy className="w-3 h-3" />
                      <span>Copy</span>
                    </>
                  )}
                </button>
              )}
            </div>

            {/* TAB 1: SCRIPT CODE */}
            {activeTab === 'code' && (
              activeScript ? (
                <div className="flex-1 flex flex-col overflow-hidden">
                  {/* File Header Details */}
                  <div className="px-5 py-2.5 border-b border-slate-800 bg-slate-900/60 flex items-center justify-between flex-shrink-0">
                    <div className="space-y-0.5">
                      <div className="flex items-center space-x-2">
                        <span className="font-mono text-sm font-semibold text-white">
                          {activeScript.fileName}
                        </span>
                        {getTestTypeBadge(activeScript.testType || (activeScript.fileName?.toLowerCase().includes('api') ? 'api' : 'unit'))}
                        {activeScript.status === 'success' ? (
                          <span className="text-xs px-2 py-0.5 rounded-full border bg-emerald-500/20 text-emerald-300 border-emerald-500/40 font-mono flex items-center space-x-1">
                            <CheckCircle2 className="w-3.5 h-3.5 mr-1 text-emerald-400 flex-shrink-0" />
                            <span>Validated • Ready to Execute</span>
                          </span>
                        ) : (
                          <span className="text-xs px-2 py-0.5 rounded-full border bg-red-500/20 text-red-300 border-red-500/40 font-mono flex items-center space-x-1">
                            <AlertCircle className="w-3.5 h-3.5 mr-1 text-red-400 flex-shrink-0" />
                            <span>Validation Failed • {activeScript.failureType || 'INVALID'}</span>
                          </span>
                        )}
                      </div>
                      {activeScript.filePath && (
                        <div className="text-[11px] text-slate-400 font-mono truncate max-w-xl">
                          {activeScript.filePath}
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Validation Error Banner */}
                  {activeScript.status !== 'success' && activeScript.errors && activeScript.errors.length > 0 && (
                    <div className="bg-red-950/70 border-b border-red-800/70 px-5 py-2.5 text-xs text-red-200 flex-shrink-0">
                      <div className="font-semibold text-red-300 flex items-center space-x-1.5 mb-1">
                        <AlertCircle className="w-4 h-4 text-red-400 flex-shrink-0" />
                        <span>Cross-Validation Failures ({activeScript.failureType || 'VALIDATION_FAILED'}):</span>
                      </div>
                      <ul className="list-disc list-inside space-y-0.5 pl-2 text-red-200/90 font-mono text-[11px]">
                        {activeScript.errors.map((err, i) => (
                          <li key={i}>{err}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* Code Body */}
                  <div className="flex-1 overflow-auto p-4 bg-slate-950 font-mono text-xs text-slate-200">
                    <pre className="leading-relaxed">
                      <code>{activeScript.content || activeScript.script || '// No code content available'}</code>
                    </pre>
                  </div>
                </div>
              ) : (
                <div className="flex-1 flex flex-col items-center justify-center text-slate-500 text-sm space-y-2">
                  <FileCode className="w-8 h-8 text-slate-600" />
                  <span>Select a test script from the left pane to view.</span>
                </div>
              )
            )}

            {/* TAB 2: EXECUTION TERMINAL */}
            {activeTab === 'execution' && (
              <div className="flex-1 flex flex-col overflow-hidden bg-slate-950 p-4">
                {executing && (
                  <div className="flex-1 flex flex-col items-center justify-center text-slate-400 space-y-3">
                    <Loader2 className="w-8 h-8 animate-spin text-emerald-400" />
                    <p className="text-sm font-medium">Executing test scripts using project test runner...</p>
                    <p className="text-xs text-slate-500">Capturing real stdout, stderr, and exit codes</p>
                  </div>
                )}

                {executionError && (
                  <div className="p-4 rounded-lg bg-red-950/50 border border-red-800/80 text-red-300 text-xs mb-4">
                    <div className="flex items-center gap-2 font-bold mb-1">
                      <AlertCircle className="w-4 h-4 text-red-400" />
                      <span>Execution Error</span>
                    </div>
                    <p>{executionError}</p>
                  </div>
                )}

                {!executing && executionResult && (
                  <div className="flex-1 flex flex-col overflow-hidden">
                    {/* Summary Header */}
                    <div className="bg-slate-900 border border-slate-800 rounded-lg p-3 mb-4 flex flex-wrap items-center justify-between gap-3 flex-shrink-0">
                      <div className="flex items-center gap-3">
                        <span className="text-xs px-2.5 py-1 rounded bg-slate-800 font-mono text-slate-300 border border-slate-700">
                          Runner: <strong className="text-white">{executionResult.runner}</strong>
                        </span>
                        <span className="text-xs text-emerald-400 font-mono font-semibold">
                          {executionResult.passed} Passed
                        </span>
                        {executionResult.failed > 0 && (
                          <span className="text-xs text-red-400 font-mono font-semibold">
                            {executionResult.failed} Failed
                          </span>
                        )}
                        <span className="text-xs text-slate-400 font-mono flex items-center gap-1">
                          <Clock className="w-3 h-3" />
                          {executionResult.durationMs}ms
                        </span>
                      </div>
                      <span className="text-xs text-slate-400 font-mono">
                        {executionResult.message}
                      </span>
                    </div>

                    {/* Execution Results List */}
                    <div className="flex-1 overflow-y-auto space-y-3 font-mono text-xs">
                      {executionResult.results?.map((res, i) => (
                        <div key={i} className="border border-slate-800 rounded-lg overflow-hidden bg-slate-900/60">
                          <div className="px-3 py-2 bg-slate-800/60 flex items-center justify-between border-b border-slate-800">
                            <div className="flex items-center gap-2 flex-wrap">
                              {res.status === 'passed' ? (
                                <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                              ) : (
                                <AlertCircle className="w-4 h-4 text-red-400" />
                              )}
                              <span className="font-bold text-slate-200">{res.scriptFile}</span>
                              {getTestTypeBadge(res.testType)}
                              {res.failureType && (
                                <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full border border-red-500/40 bg-red-500/20 text-red-300 font-mono">
                                  {res.failureType}
                                </span>
                              )}
                              {res.stage && res.stage !== 'execution' && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-mono border border-slate-700">
                                  stage: {res.stage}
                                </span>
                              )}
                            </div>
                            <div className="flex items-center gap-2 text-[11px] text-slate-400">
                              <span>Exit: {res.exitCode}</span>
                              <span>•</span>
                              <span>{res.durationMs}ms</span>
                            </div>
                          </div>

                          {/* Failure Evidence Banner */}
                          {res.evidence && res.status !== 'passed' && (
                            <div className="px-3 py-1.5 bg-red-950/40 border-b border-red-900/40 text-red-300 text-[11px] flex items-start gap-1.5">
                              <AlertCircle className="w-3.5 h-3.5 text-red-400 flex-shrink-0 mt-0.5" />
                              <span className="font-semibold text-red-200">Evidence:</span>
                              <span className="font-mono text-red-300 truncate" title={res.evidence}>{res.evidence}</span>
                            </div>
                          )}

                          {/* Command Line Executed */}
                          {res.command && (
                            <div className="px-3 py-1 bg-slate-950 text-slate-400 text-[10px] font-mono border-b border-slate-800 flex items-center gap-1.5 overflow-x-auto">
                              <Terminal className="w-3 h-3 text-slate-500 flex-shrink-0" />
                              <span className="text-slate-500 select-none">$</span>
                              <span className="text-slate-300">{res.command}</span>
                            </div>
                          )}

                          {(res.stdout || res.stderr) ? (
                            <div className="p-3 bg-black/60 text-slate-300 text-[11px] whitespace-pre-wrap max-h-48 overflow-y-auto">
                              {res.stdout && <div>{res.stdout}</div>}
                              {res.stderr && <div className="text-red-400 mt-1">{res.stderr}</div>}
                            </div>
                          ) : (
                            <div className="p-2 text-slate-500 text-[10px] italic">No console output recorded.</div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {!executing && !executionResult && !executionError && (
                  <div className="flex-1 flex flex-col items-center justify-center text-slate-500 text-sm space-y-2">
                    <Terminal className="w-8 h-8 text-slate-600" />
                    <span>Click "Run Tests" to execute generated scripts with the project's test runner.</span>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-2.5 bg-slate-800/80 border-t border-slate-700/80 flex items-center justify-between flex-shrink-0 text-xs text-slate-400">
          <div className="flex items-center space-x-2">
            <span className="w-2 h-2 rounded-full bg-emerald-400"></span>
            <span>Deterministic 1:1 Storage under <code>test scripts/&lt;group&gt;/&lt;title&gt;.{projectEnv?.file_extension?.replace('.', '') || 'ext'}</code></span>
          </div>
          <button
            onClick={onClose}
            className="px-4 py-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-white font-medium transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
