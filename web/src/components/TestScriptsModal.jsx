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
  FolderTree
} from 'lucide-react';

export default function TestScriptsModal({
  isOpen,
  onClose,
  scripts = [],
  projectEnv = null,
  loading = false,
  onRefresh,
  projectName = ''
}) {
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [copied, setCopied] = useState(false);
  const [filterText, setFilterText] = useState('');

  // Reset selection when scripts change
  useEffect(() => {
    setSelectedIdx(0);
  }, [scripts]);

  // Filtered scripts list
  const filteredScripts = useMemo(() => {
    if (!filterText.trim()) return scripts;
    const q = filterText.toLowerCase();
    return scripts.filter(
      (s) =>
        (s.testCaseId && s.testCaseId.toLowerCase().includes(q)) ||
        (s.testCaseTitle && s.testCaseTitle.toLowerCase().includes(q)) ||
        (s.target && s.target.toLowerCase().includes(q)) ||
        (s.fileName && s.fileName.toLowerCase().includes(q)) ||
        (s.group && s.group.toLowerCase().includes(q)) ||
        (s.content && s.content.toLowerCase().includes(q))
    );
  }, [scripts, filterText]);

  const activeScript = filteredScripts[selectedIdx] || filteredScripts[0] || null;

  // Handle copy script
  const handleCopyCode = (code) => {
    if (!code) return;
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (!isOpen) return null;

  const envLang = projectEnv?.language || 'auto-detected';
  const envFramework = projectEnv?.test_framework || 'standard';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="bg-slate-900 border border-slate-700/80 rounded-xl shadow-2xl w-full max-w-6xl h-[88vh] flex flex-col overflow-hidden">
        {/* Header */}
        <div className="px-6 py-4 bg-slate-800/80 border-b border-slate-700/80 flex items-center justify-between flex-shrink-0">
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
                Dynamic 1:1 executable test scripts grounded in source code
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
          {/* Left Sidebar: Script files list */}
          <div className="w-80 border-r border-slate-800 flex flex-col bg-slate-900/50 flex-shrink-0">
            {/* Search box */}
            <div className="p-3 border-b border-slate-800">
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
                  No generated test scripts found.
                </div>
              ) : (
                filteredScripts.map((s, idx) => {
                  const isSelected = idx === selectedIdx;
                  return (
                    <button
                      key={s.filePath || idx}
                      onClick={() => setSelectedIdx(idx)}
                      className={`w-full text-left p-2.5 rounded-lg text-xs transition-colors flex flex-col space-y-1 ${
                        isSelected
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
                          {s.status === 'success' ? (
                            <span className="w-2 h-2 rounded-full bg-emerald-400" title="Cross-validation passed" />
                          ) : (
                            <span className="w-2 h-2 rounded-full bg-red-400 animate-pulse" title={`Validation failed: ${s.failureType || 'INVALID'}`} />
                          )}
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 font-mono text-indigo-300">
                            {s.testCaseId}
                          </span>
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
              <span>Total: <span className="font-mono font-medium text-slate-200">{scripts.length}</span></span>
              <span className="flex items-center space-x-1.5 text-[10px] font-mono">
                <span className="text-emerald-400">
                  {scripts.filter((s) => s.status === 'success').length} valid
                </span>
                {scripts.some((s) => s.status !== 'success') && (
                  <span className="text-red-400">
                    • {scripts.filter((s) => s.status !== 'success').length} failed
                  </span>
                )}
              </span>
            </div>
          </div>

          {/* Right Main Pane: Code Viewer */}
          <div className="flex-1 flex flex-col overflow-hidden bg-slate-950">
            {activeScript ? (
              <div className="flex-1 flex flex-col overflow-hidden">
                {/* File Header Details */}
                <div className="px-5 py-3 border-b border-slate-800 bg-slate-900/60 flex items-center justify-between flex-shrink-0">
                  <div className="space-y-0.5">
                    <div className="flex items-center space-x-2">
                      <span className="font-mono text-sm font-semibold text-white">
                        {activeScript.fileName}
                      </span>
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

                  <div className="flex items-center space-x-2">
                    <button
                      onClick={() => handleCopyCode(activeScript.content || activeScript.script)}
                      className="px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 flex items-center space-x-1.5 transition-colors"
                    >
                      {copied ? (
                        <>
                          <Check className="w-3.5 h-3.5 text-emerald-400" />
                          <span>Copied</span>
                        </>
                      ) : (
                        <>
                          <Copy className="w-3.5 h-3.5" />
                          <span>Copy Script</span>
                        </>
                      )}
                    </button>
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
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-3 bg-slate-800/80 border-t border-slate-700/80 flex items-center justify-between flex-shrink-0 text-xs text-slate-400">
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
