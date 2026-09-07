import React, { useState } from 'react';
import {
  X,
  FileCode,
  Package,
  ArrowRight,
  ArrowLeft,
  Code2,
  Box,
  Globe,
  Database,
  Hash,
  Share2,
  Layers,
  CheckCircle2,
  AlertTriangle,
  ArrowUpRight,
  Download,
  Upload
} from 'lucide-react';

const REL_COLORS = {
  imports: 'bg-sky-500/20 text-sky-300 border-sky-500/40',
  calls: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40',
  extends: 'bg-violet-500/20 text-violet-300 border-violet-500/40',
  implements: 'bg-purple-500/20 text-purple-300 border-purple-500/40',
  renders: 'bg-amber-500/20 text-amber-300 border-amber-500/40',
  uses: 'bg-blue-500/20 text-blue-300 border-blue-500/40',
  references: 'bg-teal-500/20 text-teal-300 border-teal-500/40',
  routes_to: 'bg-red-500/20 text-red-300 border-red-500/40',
  handles: 'bg-rose-500/20 text-rose-300 border-rose-500/40',
  default: 'bg-slate-700/50 text-slate-300 border-slate-600'
};

export default function NodeDetails({ node, lookup, onSelectNode, onClose }) {
  if (!node) return null;

  const [activeTab, setActiveTab] = useState('all'); // 'all' | 'deps' | 'ast' | 'imports'

  const fileId = node.id;
  const data = node.data || {};
  const isPackage = data.nodeType === 'package' || data.nodeType === 'module' || data.isExternal;

  // O(1) in-memory resolution of full Code Map file artifact
  const artifact = lookup?.fileById?.get(fileId) || data.artifact || {};
  const fileInfo = artifact.file || {};
  const parseInfo = artifact.parse || {};

  const relPath = artifact.relativePath || fileInfo.relativePath || data.filePath || fileId;
  const fileName = artifact.name || fileInfo.name || data.label || fileId;
  const language = artifact.language || fileInfo.language || data.language;
  const lineCount = artifact.lineCount !== undefined ? artifact.lineCount : (fileInfo.lineCount !== undefined ? fileInfo.lineCount : data.lineCount);
  const parseStatus = artifact.parseStatus || parseInfo.status || data.parseStatus || 'success';
  const parseErrors = Array.isArray(artifact.parseErrors) ? artifact.parseErrors : (Array.isArray(parseInfo.errors) ? parseInfo.errors : []);

  // O(1) resolution of outgoing and incoming dependencies
  const outgoingDeps = lookup?.outgoingByFile?.get(fileId) || [];
  const incomingDeps = lookup?.incomingByFile?.get(fileId) || [];

  // Code Map structural collections
  const rawImports = Array.isArray(artifact.imports) ? artifact.imports : [];
  const rawDependencies = Array.isArray(artifact.dependencies) ? artifact.dependencies : [];
  const imports = rawImports.length > 0 ? rawImports : rawDependencies.map(d => ({
    source: d.target || d.source,
    importType: d.kind || d.type,
    specifiers: d.specifiers || [],
    line: d.line
  }));
  const exports = Array.isArray(artifact.exports) ? artifact.exports.map(e => ({
    ...e,
    location: e.location || (e.line ? { startLine: e.line } : undefined)
  })) : [];

  // Declarations
  const rawDeclarations = Array.isArray(artifact.declarations) ? artifact.declarations : [];
  const rawSymbols = Array.isArray(artifact.symbols) ? artifact.symbols : [];

  // Deduplicate and collect functions
  const functions = [
    ...(Array.isArray(artifact.functions) ? artifact.functions.map(f => typeof f === 'string' ? { name: f } : f) : []),
    ...rawDeclarations.filter(d => d.type === 'function' || d.type === 'method'),
    ...rawSymbols.filter(s => s.type === 'function' || s.type === 'method')
  ];
  const uniqueFunctions = [];
  const seenFns = new Set();
  for (const fn of functions) {
    const startLine = fn.line || fn.location?.startLine || 0;
    const key = `${fn.name}:${startLine}`;
    if (!seenFns.has(key)) {
      seenFns.add(key);
      uniqueFunctions.push({
        ...fn,
        location: fn.location || { startLine, endLine: fn.endLine || startLine }
      });
    }
  }

  // Deduplicate and collect classes
  const rawClasses = Array.isArray(artifact.classes) ? artifact.classes : [];
  const classes = [
    ...rawDeclarations.filter(d => d.type === 'class'),
    ...rawClasses.map(c => typeof c === 'string' ? { name: c } : c),
    ...rawSymbols.filter(s => s.type === 'class')
  ];
  const uniqueClasses = [];
  const seenClasses = new Set();
  for (const cls of classes) {
    if (cls.name && !seenClasses.has(cls.name)) {
      seenClasses.add(cls.name);
      uniqueClasses.push({
        ...cls,
        location: cls.location || (cls.line ? { startLine: cls.line } : undefined)
      });
    }
  }

  // Components & JSX
  const reactComponents = [
    ...(Array.isArray(artifact.components) ? artifact.components : []),
    ...(Array.isArray(artifact.react?.components) ? artifact.react.components : []),
    ...rawDeclarations.filter(d => d.type === 'component')
  ];
  const jsxElements = Array.isArray(artifact.react?.jsxElements) ? artifact.react.jsxElements : [];

  // Calls & References
  const calls = Array.isArray(artifact.calls) ? artifact.calls.map(c => ({
    ...c,
    location: c.location || (c.line ? { startLine: c.line } : undefined)
  })) : [];
  const references = Array.isArray(artifact.references) ? artifact.references : [];

  // Routes & DB operations
  const routes = Array.isArray(artifact.routes) ? artifact.routes.map(r => ({
    ...r,
    location: r.location || (r.line ? { startLine: r.line } : undefined)
  })) : [];
  const databaseOperations = Array.isArray(artifact.databaseOperations) ? artifact.databaseOperations.map(d => ({
    ...d,
    location: d.location || (d.line ? { startLine: d.line } : undefined)
  })) : [];

  // Non-function/class symbols
  const otherSymbols = [
    ...rawDeclarations.filter(d => d.type !== 'function' && d.type !== 'method' && d.type !== 'class' && d.type !== 'component'),
    ...rawSymbols.filter(s => s.type !== 'function' && s.type !== 'method' && s.type !== 'class')
  ];

  return (
    <div className="h-full bg-slate-900 border-l border-slate-800 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="p-5 border-b border-slate-800 bg-slate-950/80 flex-shrink-0">
        <div className="flex justify-between items-start">
          <div className="flex-1 pr-3 min-w-0">
            <div className="flex items-center gap-2 mb-1.5">
              {isPackage ? (
                <Package className="w-5 h-5 text-indigo-400 flex-shrink-0" />
              ) : (
                <FileCode className="w-5 h-5 text-sky-400 flex-shrink-0" />
              )}
              <h2 className="text-lg font-bold text-white truncate" title={fileName}>
                {fileName}
              </h2>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <span
                className="inline-block px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wider text-white shadow-sm"
                style={{ backgroundColor: isPackage ? '#4f46e5' : '#0284c7' }}
              >
                {isPackage ? 'External Package' : 'Source File'}
              </span>

              {language && (
                <span className="bg-sky-950 text-sky-300 border border-sky-800/60 text-[11px] font-mono px-2 py-0.5 rounded uppercase">
                  {language}
                </span>
              )}

              {lineCount !== undefined && lineCount > 0 && (
                <span className="text-slate-400 text-xs">
                  {lineCount} lines
                </span>
              )}

              {parseStatus === 'success' ? (
                <span className="flex items-center gap-1 text-emerald-400 text-xs font-medium">
                  <CheckCircle2 className="w-3.5 h-3.5" /> Parsed
                </span>
              ) : (
                <span className="flex items-center gap-1 text-red-400 text-xs font-medium">
                  <AlertTriangle className="w-3.5 h-3.5" /> Error
                </span>
              )}
            </div>
          </div>

          <button
            onClick={onClose}
            className="p-1 hover:bg-slate-800 rounded-md text-slate-400 hover:text-white transition-colors"
            title="Close details"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Repository Relative Path */}
        {relPath && (
          <div className="mt-3 bg-slate-900/90 p-2 rounded border border-slate-800 flex items-center justify-between gap-2">
            <span className="text-[11px] text-slate-400 font-mono break-all">{relPath}</span>
          </div>
        )}

        {/* Navigation Filter Tabs */}
        <div className="flex gap-1 mt-4 pt-3 border-t border-slate-800/70 text-xs">
          <button
            onClick={() => setActiveTab('all')}
            className={`px-2.5 py-1 rounded font-medium transition-colors ${
              activeTab === 'all' ? 'bg-sky-600 text-white' : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            All Info
          </button>
          <button
            onClick={() => setActiveTab('deps')}
            className={`px-2.5 py-1 rounded font-medium transition-colors flex items-center gap-1 ${
              activeTab === 'deps' ? 'bg-sky-600 text-white' : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            Dependencies
            <span className="text-[10px] bg-slate-800 px-1.5 py-0.2 rounded-full">
              {outgoingDeps.length + incomingDeps.length}
            </span>
          </button>
          <button
            onClick={() => setActiveTab('ast')}
            className={`px-2.5 py-1 rounded font-medium transition-colors flex items-center gap-1 ${
              activeTab === 'ast' ? 'bg-sky-600 text-white' : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            Code Structure
            <span className="text-[10px] bg-slate-800 px-1.5 py-0.2 rounded-full">
              {uniqueFunctions.length + uniqueClasses.length + reactComponents.length}
            </span>
          </button>
          <button
            onClick={() => setActiveTab('imports')}
            className={`px-2.5 py-1 rounded font-medium transition-colors flex items-center gap-1 ${
              activeTab === 'imports' ? 'bg-sky-600 text-white' : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            Imports
            <span className="text-[10px] bg-slate-800 px-1.5 py-0.2 rounded-full">
              {imports.length}
            </span>
          </button>
        </div>
      </div>

      {/* Scrollable Content Body */}
      <div className="flex-1 overflow-y-auto p-5 space-y-6">
        {/* Parse Errors if any */}
        {parseErrors.length > 0 && (
          <div className="bg-red-950/40 border border-red-800/50 p-3 rounded-lg text-xs text-red-200 space-y-1">
            <p className="font-semibold flex items-center gap-1.5 text-red-400">
              <AlertTriangle className="w-4 h-4" /> Parse Warnings/Errors
            </p>
            {parseErrors.map((err, i) => (
              <p key={i} className="font-mono text-red-300/80">{err}</p>
            ))}
          </div>
        )}

        {/* 1. DEPENDENCIES SECTION (Requirements 2, 3, 11) */}
        {(activeTab === 'all' || activeTab === 'deps') && (
          <div className="space-y-4">
            <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5 border-b border-slate-800 pb-1.5">
              <Share2 className="w-3.5 h-3.5 text-brand-400" />
              Dependencies ({outgoingDeps.length + incomingDeps.length})
            </h3>

            {/* Outgoing: Files this file depends on */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-medium text-slate-300 flex items-center gap-1">
                  <Upload className="w-3.5 h-3.5 text-sky-400" /> Depends On (Outgoing)
                </span>
                <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-slate-800 text-slate-300">
                  {outgoingDeps.length}
                </span>
              </div>

              {outgoingDeps.length === 0 ? (
                <p className="text-xs text-slate-500 italic bg-slate-950/40 p-2.5 rounded border border-slate-800/40">
                  This file has no outgoing dependencies.
                </p>
              ) : (
                <div className="space-y-2">
                  {outgoingDeps.map((dep, idx) => (
                    <div
                      key={idx}
                      onClick={() => !dep.isExternal && onSelectNode && onSelectNode(dep.targetId)}
                      className={`p-2.5 rounded-lg border text-xs transition-colors ${
                        dep.isExternal
                          ? 'bg-slate-900/60 border-indigo-900/40'
                          : 'bg-slate-900 border-slate-800 hover:border-sky-500 cursor-pointer group'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-semibold text-slate-200 group-hover:text-sky-400 transition-colors flex items-center gap-1">
                          {dep.targetName}
                          {!dep.isExternal && <ArrowUpRight className="w-3 h-3 text-slate-500 group-hover:text-sky-400" />}
                        </span>
                        <div className="flex flex-wrap gap-1">
                          {dep.relationships.map((rel, rIdx) => (
                            <span
                              key={rIdx}
                              className={`text-[10px] uppercase font-semibold px-2 py-0.5 rounded border ${
                                REL_COLORS[rel] || REL_COLORS.default
                              }`}
                            >
                              {rel}
                            </span>
                          ))}
                        </div>
                      </div>
                      {dep.targetPath && (
                        <p className="text-[11px] text-slate-400 font-mono mt-1 truncate">{dep.targetPath}</p>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Incoming: Files that depend on this file */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-medium text-slate-300 flex items-center gap-1">
                  <Download className="w-3.5 h-3.5 text-emerald-400" /> Depended On By (Incoming)
                </span>
                <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-slate-800 text-slate-300">
                  {incomingDeps.length}
                </span>
              </div>

              {incomingDeps.length === 0 ? (
                <p className="text-xs text-slate-500 italic bg-slate-950/40 p-2.5 rounded border border-slate-800/40">
                  No other files depend on this file.
                </p>
              ) : (
                <div className="space-y-2">
                  {incomingDeps.map((dep, idx) => (
                    <div
                      key={idx}
                      onClick={() => onSelectNode && onSelectNode(dep.sourceId)}
                      className="p-2.5 rounded-lg border bg-slate-900 border-slate-800 hover:border-emerald-500 cursor-pointer transition-colors group text-xs"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-semibold text-slate-200 group-hover:text-emerald-400 transition-colors flex items-center gap-1">
                          {dep.sourceName}
                          <ArrowUpRight className="w-3 h-3 text-slate-500 group-hover:text-emerald-400" />
                        </span>
                        <div className="flex flex-wrap gap-1">
                          {dep.relationships.map((rel, rIdx) => (
                            <span
                              key={rIdx}
                              className={`text-[10px] uppercase font-semibold px-2 py-0.5 rounded border ${
                                REL_COLORS[rel] || REL_COLORS.default
                              }`}
                            >
                              {rel}
                            </span>
                          ))}
                        </div>
                      </div>
                      {dep.sourcePath && (
                        <p className="text-[11px] text-slate-400 font-mono mt-1 truncate">{dep.sourcePath}</p>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* 2. IMPORTS SECTION (Requirement 6) */}
        {(activeTab === 'all' || activeTab === 'imports') && (
          <div className="space-y-3">
            <div className="flex items-center justify-between border-b border-slate-800 pb-1.5">
              <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                <Upload className="w-3.5 h-3.5 text-sky-400" />
                Imports
              </h3>
              <span className="text-xs bg-sky-950 text-sky-300 font-semibold px-2 py-0.5 rounded-full border border-sky-800/50">
                {imports.length}
              </span>
            </div>

            {imports.length === 0 ? (
              <p className="text-xs text-slate-500 italic bg-slate-950/40 p-2.5 rounded border border-slate-800/40">
                No import statements extracted in this file.
              </p>
            ) : (
              <div className="space-y-2 max-h-64 overflow-y-auto">
                {imports.map((imp, idx) => {
                  const src = imp.source || imp.modulePath || '';
                  const line = imp.location?.startLine || imp.line;
                  const specifiers = Array.isArray(imp.specifiers)
                    ? imp.specifiers.map(s => typeof s === 'string' ? s : s.name || s.alias || '')
                    : Array.isArray(imp.importedNames) ? imp.importedNames : [];

                  return (
                    <div key={idx} className="bg-slate-900 p-2.5 rounded-lg border border-slate-800 text-xs">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-mono text-sky-300 font-semibold truncate" title={src}>
                          {src}
                        </span>
                        {line && (
                          <span className="text-[10px] text-slate-500 font-mono flex-shrink-0">
                            L{line}
                          </span>
                        )}
                      </div>
                      {specifiers.length > 0 && (
                        <div className="mt-1 flex flex-wrap gap-1">
                          {specifiers.map((s, sIdx) => (
                            <span key={sIdx} className="bg-slate-800 text-slate-300 text-[10px] font-mono px-1.5 py-0.5 rounded">
                              {s}
                            </span>
                          ))}
                        </div>
                      )}
                      {imp.importType && (
                        <span className="mt-1 inline-block text-[9px] uppercase tracking-wider text-slate-500 font-sans">
                          type: {imp.importType}
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* 3. EXPORTS SECTION */}
        {(activeTab === 'all' || activeTab === 'imports') && exports.length > 0 && (
          <div className="space-y-3">
            <div className="flex items-center justify-between border-b border-slate-800 pb-1.5">
              <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                <Download className="w-3.5 h-3.5 text-emerald-400" />
                Exports
              </h3>
              <span className="text-xs bg-emerald-950 text-emerald-300 font-semibold px-2 py-0.5 rounded-full border border-emerald-800/50">
                {exports.length}
              </span>
            </div>

            <div className="space-y-1.5 max-h-48 overflow-y-auto">
              {exports.map((exp, idx) => (
                <div key={idx} className="bg-slate-900 p-2 rounded-lg border border-slate-800 text-xs flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] uppercase font-semibold px-1.5 py-0.5 rounded bg-slate-800 text-emerald-400">
                      {exp.exportType || 'export'}
                    </span>
                    <span className="font-mono text-slate-200">{exp.name || '(default)'}</span>
                  </div>
                  {exp.location?.startLine && (
                    <span className="text-[10px] text-slate-500 font-mono">L{exp.location.startLine}</span>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 4. FUNCTIONS & METHODS (Requirement 7) */}
        {(activeTab === 'all' || activeTab === 'ast') && (
          <div className="space-y-3">
            <div className="flex items-center justify-between border-b border-slate-800 pb-1.5">
              <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                <Code2 className="w-3.5 h-3.5 text-emerald-400" />
                Functions & Methods
              </h3>
              <span className="text-xs bg-emerald-950 text-emerald-300 font-semibold px-2 py-0.5 rounded-full border border-emerald-800/50">
                {uniqueFunctions.length}
              </span>
            </div>

            {uniqueFunctions.length === 0 ? (
              <p className="text-xs text-slate-500 italic bg-slate-950/40 p-2.5 rounded border border-slate-800/40">
                No functions or methods extracted.
              </p>
            ) : (
              <div className="space-y-1.5 max-h-56 overflow-y-auto">
                {uniqueFunctions.map((fn, idx) => {
                  const startL = fn.location?.startLine;
                  const endL = fn.location?.endLine;
                  return (
                    <div key={idx} className="bg-slate-900 p-2 rounded-lg border border-slate-800 text-xs flex items-center justify-between gap-2">
                      <span className="font-mono text-emerald-300 font-medium truncate">
                        {fn.name}()
                      </span>
                      {startL && (
                        <span className="text-[10px] text-slate-500 font-mono flex-shrink-0">
                          L{startL}{endL && endL !== startL ? `-${endL}` : ''}
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* 5. CLASSES (Requirement 8) */}
        {(activeTab === 'all' || activeTab === 'ast') && uniqueClasses.length > 0 && (
          <div className="space-y-3">
            <div className="flex items-center justify-between border-b border-slate-800 pb-1.5">
              <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                <Box className="w-3.5 h-3.5 text-violet-400" />
                Classes
              </h3>
              <span className="text-xs bg-violet-950 text-violet-300 font-semibold px-2 py-0.5 rounded-full border border-violet-800/50">
                {uniqueClasses.length}
              </span>
            </div>

            <div className="space-y-2 max-h-48 overflow-y-auto">
              {uniqueClasses.map((cls, idx) => (
                <div key={idx} className="bg-slate-900 p-2.5 rounded-lg border border-slate-800 text-xs">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-violet-300 font-semibold">
                      class {cls.name}
                    </span>
                    {cls.location?.startLine && (
                      <span className="text-[10px] text-slate-500 font-mono">L{cls.location.startLine}</span>
                    )}
                  </div>
                  {cls.extends && (
                    <p className="text-[11px] text-slate-400 font-mono mt-1">
                      extends <span className="text-slate-200">{cls.extends}</span>
                    </p>
                  )}
                  {Array.isArray(cls.implements) && cls.implements.length > 0 && (
                    <p className="text-[11px] text-slate-400 font-mono mt-1">
                      implements <span className="text-slate-200">{cls.implements.join(', ')}</span>
                    </p>
                  )}
                  {Array.isArray(cls.methods) && cls.methods.length > 0 && (
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {cls.methods.map((m, mIdx) => (
                        <span key={mIdx} className="bg-slate-800 text-violet-200 text-[10px] font-mono px-1.5 py-0.5 rounded">
                          {typeof m === 'string' ? m : m.name}()
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 6. COMPONENTS (Requirement 9) */}
        {(activeTab === 'all' || activeTab === 'ast') && (reactComponents.length > 0 || jsxElements.length > 0) && (
          <div className="space-y-3">
            <div className="flex items-center justify-between border-b border-slate-800 pb-1.5">
              <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                <Layers className="w-3.5 h-3.5 text-amber-400" />
                UI Components & Elements
              </h3>
              <span className="text-xs bg-amber-950 text-amber-300 font-semibold px-2 py-0.5 rounded-full border border-amber-800/50">
                {reactComponents.length}
              </span>
            </div>

            <div className="flex flex-wrap gap-1.5 max-h-40 overflow-y-auto">
              {reactComponents.map((comp, idx) => (
                <span key={idx} className="bg-amber-950/60 border border-amber-700/40 text-amber-300 font-mono text-xs px-2 py-1 rounded">
                  &lt;{typeof comp === 'string' ? comp : comp.name} /&gt;
                </span>
              ))}
            </div>
          </div>
        )}

        {/* 7. CALLS & REFERENCES (Requirement 10) */}
        {(activeTab === 'all' || activeTab === 'ast') && calls.length > 0 && (
          <div className="space-y-3">
            <div className="flex items-center justify-between border-b border-slate-800 pb-1.5">
              <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                <Share2 className="w-3.5 h-3.5 text-blue-400" />
                Function Calls ({calls.length})
              </h3>
            </div>

            <div className="space-y-1.5 max-h-48 overflow-y-auto">
              {calls.map((c, idx) => (
                <div key={idx} className="bg-slate-900 p-2 rounded-lg border border-slate-800 text-xs flex items-center justify-between gap-2">
                  <span className="font-mono text-blue-300 truncate" title={c.callee}>
                    {c.callee}()
                  </span>
                  {c.location?.startLine && (
                    <span className="text-[10px] text-slate-500 font-mono flex-shrink-0">
                      L{c.location.startLine}
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 8. ROUTES & ENDPOINTS */}
        {(activeTab === 'all' || activeTab === 'ast') && routes.length > 0 && (
          <div className="space-y-3">
            <div className="flex items-center justify-between border-b border-slate-800 pb-1.5">
              <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                <Globe className="w-3.5 h-3.5 text-rose-400" />
                Routes & Endpoints
              </h3>
              <span className="text-xs bg-rose-950 text-rose-300 font-semibold px-2 py-0.5 rounded-full border border-rose-800/50">
                {routes.length}
              </span>
            </div>

            <div className="space-y-1.5 max-h-40 overflow-y-auto">
              {routes.map((route, idx) => {
                const method = route.method || 'ROUTE';
                const pathStr = route.path || (typeof route === 'string' ? route : '');
                return (
                  <div key={idx} className="bg-slate-900 p-2 rounded-lg border border-slate-800 text-xs flex items-center justify-between gap-2">
                    <div className="flex items-center gap-1.5 truncate">
                      <span className="text-[9px] uppercase font-bold px-1.5 py-0.5 rounded bg-rose-950 text-rose-300 border border-rose-800/50">
                        {method}
                      </span>
                      <span className="font-mono text-slate-200 truncate">{pathStr}</span>
                    </div>
                    {route.location?.startLine && (
                      <span className="text-[10px] text-slate-500 font-mono">L{route.location.startLine}</span>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* 9. DATABASE OPERATIONS */}
        {(activeTab === 'all' || activeTab === 'ast') && databaseOperations.length > 0 && (
          <div className="space-y-3">
            <div className="flex items-center justify-between border-b border-slate-800 pb-1.5">
              <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                <Database className="w-3.5 h-3.5 text-orange-400" />
                Database Operations
              </h3>
              <span className="text-xs bg-orange-950 text-orange-300 font-semibold px-2 py-0.5 rounded-full border border-orange-800/50">
                {databaseOperations.length}
              </span>
            </div>

            <div className="space-y-1.5 max-h-40 overflow-y-auto">
              {databaseOperations.map((op, idx) => (
                <div key={idx} className="bg-slate-900 p-2 rounded-lg border border-slate-800 text-xs flex items-center justify-between gap-2">
                  <div className="flex items-center gap-1.5 truncate">
                    <span className="text-[10px] uppercase font-mono px-1.5 py-0.5 rounded bg-orange-950 text-orange-300 border border-orange-800/50">
                      {op.operation || 'db'}
                    </span>
                    <span className="font-mono text-slate-200 truncate">{op.callee || String(op)}</span>
                  </div>
                  {op.location?.startLine && (
                    <span className="text-[10px] text-slate-500 font-mono">L{op.location.startLine}</span>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 10. OTHER SYMBOLS */}
        {(activeTab === 'all' || activeTab === 'ast') && otherSymbols.length > 0 && (
          <div className="space-y-3">
            <div className="flex items-center justify-between border-b border-slate-800 pb-1.5">
              <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                <Hash className="w-3.5 h-3.5 text-sky-400" />
                Other Declared Symbols
              </h3>
              <span className="text-xs bg-sky-950 text-sky-300 font-semibold px-2 py-0.5 rounded-full border border-sky-800/50">
                {otherSymbols.length}
              </span>
            </div>

            <div className="flex flex-wrap gap-1 max-h-36 overflow-y-auto">
              {otherSymbols.map((s, idx) => (
                <span key={idx} className="bg-slate-800 text-slate-300 font-mono text-xs px-2 py-0.5 rounded">
                  {s.name}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
