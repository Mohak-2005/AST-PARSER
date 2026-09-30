import React, { useState, useEffect, useMemo } from 'react';
import {
  X,
  Code2,
  FileCode,
  Copy,
  Check,
  RefreshCw,
  AlertCircle,
  CheckCircle2,
  Layers,
  ChevronRight,
  Search,
  FileText
} from 'lucide-react';

export default function RetrievedCodeModal({
  isOpen,
  onClose,
  files = [],
  loading = false,
  onRefresh,
  projectName = ''
}) {
  const [selectedFileIdx, setSelectedFileIdx] = useState(0);
  const [selectedCaseIdx, setSelectedCaseIdx] = useState(0);
  const [copied, setCopied] = useState(false);
  const [filterText, setFilterText] = useState('');

  // Reset selection when files change
  useEffect(() => {
    setSelectedFileIdx(0);
    setSelectedCaseIdx(0);
  }, [files]);

  const currentFile = files[selectedFileIdx] || null;

  // Extract test cases from current file content
  const testCases = useMemo(() => {
    if (!currentFile || !currentFile.content) return [];
    const content = currentFile.content;
    if (Array.isArray(content.testCases) && content.testCases.length > 0) {
      return content.testCases.map((tc) => ({
        ...tc,
        testType: tc.testType || content.testType || 'unit',
        route: tc.route || tc.target?.route || content.route,
        httpMethod: tc.httpMethod || tc.target?.httpMethod || content.httpMethod,
      }));
    }
    // Fallback: single test case structure
    if (content.testCaseId) {
      return [
        {
          testCaseId: content.testCaseId,
          testType: content.testType || content.target?.testType || 'unit',
          route: content.route || content.target?.route,
          httpMethod: content.httpMethod || content.target?.httpMethod,
          title: content.group || currentFile.fileName,
          target: content.target || {
            file: content.retrieval?.primaryFile,
            symbol: content.retrieval?.primarySymbol,
            route: content.route,
            httpMethod: content.httpMethod,
          },
          retrieval: content.retrieval,
          source: content.source,
          metadata: content.metadata,
          warnings: content.warnings || []
        }
      ];
    }
    return [];
  }, [currentFile]);

  const activeTestCase = testCases[selectedCaseIdx] || testCases[0] || null;

  // Filtered files list
  const filteredFiles = useMemo(() => {
    if (!filterText.trim()) return files;
    const q = filterText.toLowerCase();
    return files.filter(
      (f) =>
        f.fileName.toLowerCase().includes(q) ||
        (f.content?.group && f.content.group.toLowerCase().includes(q))
    );
  }, [files, filterText]);

  // Handle copy source code
  const handleCopyCode = (code) => {
    if (!code) return;
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (!isOpen) return null;

  const retrievalStatus =
    activeTestCase?.retrieval?.status ||
    (activeTestCase?.source?.files?.length > 0 ? 'resolved' : 'unknown');

  const statusBadgeColor =
    retrievalStatus === 'resolved'
      ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40'
      : retrievalStatus === 'partially_resolved'
      ? 'bg-amber-500/20 text-amber-300 border-amber-500/40'
      : 'bg-rose-500/20 text-rose-300 border-rose-500/40';

  const primarySourceFile =
    activeTestCase?.source?.files?.find((f) => f.reason === 'primary_target') ||
    activeTestCase?.source?.files?.[0] ||
    null;

  const primarySourceCode = primarySourceFile?.sourceCode || '';
  const relatedFiles =
    activeTestCase?.metadata?.relatedFiles ||
    activeTestCase?.retrieval?.relatedFiles ||
    [];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4 animate-fade-in">
      <div className="bg-slate-900 border border-slate-700 w-full max-w-6xl h-[85vh] rounded-xl shadow-2xl flex flex-col overflow-hidden">
        {/* Header */}
        <div className="h-16 px-6 bg-slate-950 border-b border-slate-800 flex items-center justify-between flex-shrink-0">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-indigo-600 flex items-center justify-center text-white">
              <Code2 className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-base font-bold text-slate-100 flex items-center gap-2">
                Retrieved Source Code
                <span className="text-xs px-2 py-0.5 rounded bg-slate-800 text-slate-400 font-mono font-normal">
                  code retrieved/
                </span>
              </h2>
              <p className="text-xs text-slate-400">
                Minimum sufficient source code retrieved by Local Agent for test implementation
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {onRefresh && (
              <button
                onClick={onRefresh}
                disabled={loading}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 rounded-lg border border-slate-700 transition-colors disabled:opacity-50"
                title="Re-run retrieval engine on disk"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
                Re-retrieve
              </button>
            )}
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Body Content */}
        <div className="flex-1 flex overflow-hidden">
          {/* Left Sidebar: File & Test Case Selector */}
          <div className="w-72 bg-slate-950/60 border-r border-slate-800 flex flex-col flex-shrink-0">
            <div className="p-3 border-b border-slate-800/80">
              <div className="relative">
                <Search className="w-3.5 h-3.5 absolute left-2.5 top-2.5 text-slate-500" />
                <input
                  type="text"
                  placeholder="Filter artifacts..."
                  value={filterText}
                  onChange={(e) => setFilterText(e.target.value)}
                  className="w-full bg-slate-900 border border-slate-700 rounded-md pl-8 pr-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                />
              </div>
            </div>

            <div className="flex-1 overflow-y-auto p-2 space-y-1">
              {filteredFiles.length === 0 ? (
                <div className="p-4 text-center text-xs text-slate-500">
                  {loading ? 'Retrieving source code...' : 'No retrieved code files found'}
                </div>
              ) : (
                filteredFiles.map((file, fIdx) => {
                  const isSelected = files.indexOf(file) === selectedFileIdx;
                  const count = file.content?.testCases?.length || 1;
                  const topType = (file.content?.testType || file.content?.testCases?.[0]?.testType || 'unit').toLowerCase();
                  return (
                    <div key={file.fileName} className="space-y-0.5">
                      <button
                        onClick={() => {
                          setSelectedFileIdx(files.indexOf(file));
                          setSelectedCaseIdx(0);
                        }}
                        className={`w-full text-left px-2.5 py-2 rounded-lg text-xs flex items-center justify-between transition-colors ${
                          isSelected
                            ? 'bg-indigo-600/20 text-indigo-300 font-medium border border-indigo-500/30'
                            : 'text-slate-300 hover:bg-slate-800/60 hover:text-white'
                        }`}
                      >
                        <div className="flex items-center gap-2 truncate">
                          <FileCode className="w-3.5 h-3.5 flex-shrink-0 text-slate-400" />
                          <span className="truncate">{file.fileName}</span>
                        </div>
                        <div className="flex items-center gap-1.5">
                          <span className={`text-[9px] px-1.5 py-0.2 rounded font-mono font-semibold uppercase ${
                            topType === 'api'
                              ? 'bg-amber-500/20 text-amber-300'
                              : topType === 'functional'
                              ? 'bg-cyan-500/20 text-cyan-300'
                              : 'bg-emerald-500/20 text-emerald-300'
                          }`}>
                            {topType === 'api' ? 'API' : topType === 'functional' ? 'FUNC' : 'UNIT'}
                          </span>
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400">
                            {count}
                          </span>
                        </div>
                      </button>

                      {/* Sub-cases list if current file selected */}
                      {isSelected && testCases.length > 1 && (
                        <div className="pl-4 pr-1 py-1 space-y-1 border-l-2 border-indigo-500/30 ml-3">
                          {testCases.map((tc, cIdx) => {
                            const subType = (tc.testType || topType).toLowerCase();
                            return (
                              <button
                                key={tc.testCaseId || cIdx}
                                onClick={() => setSelectedCaseIdx(cIdx)}
                                className={`w-full text-left px-2 py-1 rounded text-[11px] truncate flex items-center justify-between transition-colors ${
                                  selectedCaseIdx === cIdx
                                    ? 'bg-indigo-600/30 text-indigo-200 font-medium'
                                    : 'text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'
                                }`}
                              >
                                <span className="truncate">{tc.testCaseId || `TC-${cIdx + 1}`}</span>
                                <div className="flex items-center gap-1">
                                  <span className={`text-[8px] px-1 rounded font-mono ${
                                    subType === 'api'
                                      ? 'bg-amber-500/20 text-amber-300'
                                      : subType === 'functional'
                                      ? 'bg-cyan-500/20 text-cyan-300'
                                      : 'bg-emerald-500/20 text-emerald-300'
                                  }`}>
                                    {subType === 'api' ? 'API' : subType === 'functional' ? 'FUNC' : 'UNIT'}
                                  </span>
                                  <span className="text-[9px] text-slate-500 uppercase">
                                    {tc.retrieval?.status || 'ok'}
                                  </span>
                                </div>
                              </button>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  );
                })
              )}
            </div>

            <div className="p-3 border-t border-slate-800 bg-slate-950 text-[11px] text-slate-500 flex justify-between items-center">
              <span>Artifacts: {files.length}</span>
              <span>Total Tests: {files.reduce((acc, f) => acc + (f.content?.testCases?.length || 1), 0)}</span>
            </div>
          </div>

          {/* Right Main Panel: Inspection & Code Viewer */}
          <div className="flex-1 flex flex-col overflow-y-auto bg-slate-900">
            {activeTestCase ? (
              <div className="p-6 space-y-6">
                {/* Meta Banner */}
                <div className="bg-slate-800/80 border border-slate-700/80 rounded-xl p-4">
                  <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-sm font-bold text-indigo-400">
                        {activeTestCase.testCaseId || 'TC-001'}
                      </span>
                      <span className="text-slate-500">•</span>
                      <span className="text-sm font-semibold text-slate-100">
                        {activeTestCase.title || currentFile?.fileName}
                      </span>
                      {(() => {
                        const tcType = (activeTestCase.testType || currentFile?.content?.testType || 'unit').toLowerCase();
                        return (
                          <span className={`px-2 py-0.5 rounded text-[10px] font-mono font-bold uppercase tracking-wider border ${
                            tcType === 'api'
                              ? 'bg-amber-500/20 text-amber-300 border-amber-500/40'
                              : tcType === 'functional'
                              ? 'bg-cyan-500/20 text-cyan-300 border-cyan-500/40'
                              : 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40'
                          }`}>
                            {tcType === 'api' ? 'API' : tcType === 'functional' ? 'FUNC' : 'UNIT'}
                          </span>
                        );
                      })()}
                    </div>

                    <div className="flex items-center gap-2">
                      <span
                        className={`px-2.5 py-0.5 rounded-full text-xs font-semibold uppercase tracking-wider border ${statusBadgeColor}`}
                      >
                        {retrievalStatus}
                      </span>
                      <span className="px-2 py-0.5 rounded bg-slate-700/60 text-slate-300 text-xs font-mono">
                        Depth: {activeTestCase.retrieval?.dependencyDepth ?? 0}
                      </span>
                    </div>
                  </div>

                  {activeTestCase.description && (
                    <p className="text-xs text-slate-400 mb-3 leading-relaxed">
                      {activeTestCase.description}
                    </p>
                  )}

                  {/* Structural Context Grid */}
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs pt-3 border-t border-slate-700/50">
                    <div>
                      <span className="text-slate-500">Target File: </span>
                      <span className="font-mono text-slate-300 font-medium">
                        {activeTestCase.target?.file || activeTestCase.retrieval?.primaryFile || 'N/A'}
                      </span>
                    </div>
                    <div>
                      <span className="text-slate-500">Target Symbol: </span>
                      <span className="font-mono text-sky-400 font-medium">
                        {activeTestCase.target?.symbol || activeTestCase.retrieval?.primarySymbol || 'N/A'}
                      </span>
                    </div>
                    <div>
                      <span className="text-slate-500">Test Type: </span>
                      <span className="capitalize font-semibold text-slate-300">
                        {activeTestCase.testType || currentFile?.content?.testType || 'unit'}
                      </span>
                    </div>
                    <div>
                      <span className="text-slate-500">Artifact File: </span>
                      <span className="font-mono text-slate-400">
                        {currentFile?.fileName || 'N/A'}
                      </span>
                    </div>

                    {/* API route/method banner if applicable */}
                    {(activeTestCase.target?.route || activeTestCase.route || currentFile?.content?.route || currentFile?.content?.target?.route) && (
                      <div className="col-span-1 md:col-span-2 flex items-center gap-2 bg-slate-900/90 p-2 rounded-lg border border-amber-500/30 mt-1">
                        <span className="text-[10px] px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 font-mono font-bold uppercase">
                          {activeTestCase.target?.httpMethod || activeTestCase.httpMethod || currentFile?.content?.httpMethod || 'ANY'}
                        </span>
                        <span className="font-mono text-xs text-amber-200">
                          {activeTestCase.target?.route || activeTestCase.route || currentFile?.content?.route || currentFile?.content?.target?.route}
                        </span>
                      </div>
                    )}
                  </div>

                  {/* Warnings alert if present */}
                  {activeTestCase.warnings && activeTestCase.warnings.length > 0 && (
                    <div className="mt-3 p-2.5 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-300 text-xs flex items-start gap-2">
                      <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5 text-amber-400" />
                      <div>
                        {activeTestCase.warnings.map((w, idx) => (
                          <div key={idx}>{w}</div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                {/* Retrieved Source Code Viewer */}
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <FileText className="w-4 h-4 text-slate-400" />
                      <span className="text-xs font-semibold text-slate-300 uppercase tracking-wide">
                        Retrieved Project Source
                      </span>
                      {primarySourceFile?.path && (
                        <span className="text-xs font-mono text-slate-500">
                          ({primarySourceFile.path})
                        </span>
                      )}
                    </div>
                    <button
                      onClick={() => handleCopyCode(primarySourceCode)}
                      disabled={!primarySourceCode}
                      className="flex items-center gap-1.5 px-2.5 py-1 text-xs text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 rounded-md border border-slate-700 transition-colors disabled:opacity-40"
                    >
                      {copied ? (
                        <>
                          <Check className="w-3.5 h-3.5 text-emerald-400" />
                          <span className="text-emerald-400">Copied</span>
                        </>
                      ) : (
                        <>
                          <Copy className="w-3.5 h-3.5" />
                          <span>Copy Source</span>
                        </>
                      )}
                    </button>
                  </div>

                  {primarySourceCode ? (
                    <div className="bg-slate-950 border border-slate-800 rounded-xl overflow-hidden font-mono text-xs">
                      <div className="p-4 overflow-x-auto text-slate-200 leading-relaxed whitespace-pre max-h-[420px]">
                        {primarySourceCode}
                      </div>
                    </div>
                  ) : (
                    <div className="p-8 text-center text-xs text-slate-500 bg-slate-950 rounded-xl border border-slate-800">
                      No source code retrieved for this unit target.
                    </div>
                  )}
                </div>

                {/* Related Source Files / Dependencies */}
                {relatedFiles.length > 0 && (
                  <div className="bg-slate-800/40 border border-slate-800 rounded-lg p-3">
                    <div className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2 flex items-center gap-1.5">
                      <Layers className="w-3.5 h-3.5" />
                      Related Internal Dependencies ({relatedFiles.length})
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {relatedFiles.map((rf, idx) => (
                        <span
                          key={idx}
                          className="px-2 py-0.5 rounded bg-slate-800 text-slate-300 text-xs font-mono border border-slate-700/60"
                        >
                          {rf}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div className="h-full flex items-center justify-center p-8 text-slate-500 text-xs">
                Select an artifact from the left sidebar to inspect retrieved source code.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
