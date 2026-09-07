import React from 'react';
import { X, ArrowRight, ShieldCheck, FileSearch, Layers } from 'lucide-react';

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

export default function EdgeDetails({ edge, onClose }) {
  if (!edge) return null;

  const data = edge.data || {};
  const confidence = data.confidence !== undefined ? data.confidence : 1;
  const confColor = confidence > 0.8 ? 'bg-emerald-500' : confidence > 0.5 ? 'bg-amber-500' : 'bg-red-500';

  const relationships = data.relationships || (data.relationship ? data.relationship.split(' + ') : ['depends_on']);
  const evidenceList = Array.isArray(data.evidenceList) && data.evidenceList.length > 0
    ? data.evidenceList
    : (data.evidence ? [data.evidence] : []);
  const rawEdges = data.rawEdges || [];

  return (
    <div className="h-full bg-slate-800 border-l border-slate-700 p-6 flex flex-col overflow-y-auto">
      {/* Header */}
      <div className="flex justify-between items-start mb-6">
        <div className="flex-1 pr-2">
          {/* Badges for each relationship */}
          <div className="flex flex-wrap gap-1.5 mb-3">
            {relationships.map((rel, idx) => (
              <span
                key={idx}
                className={`px-2.5 py-0.5 rounded-full text-xs font-semibold uppercase tracking-wider border ${
                  REL_COLORS[rel.trim()] || REL_COLORS.default
                }`}
              >
                {rel.trim()}
              </span>
            ))}
          </div>

          <div className="flex items-center gap-2 text-sm text-slate-200 font-mono break-all bg-slate-900/60 p-2.5 rounded-lg border border-slate-700/60">
            <span className="truncate max-w-[140px] text-sky-400 font-medium" title={edge.source}>
              {edge.source}
            </span>
            <ArrowRight className="w-4 h-4 flex-shrink-0 text-slate-500" />
            <span className="truncate max-w-[140px] text-sky-400 font-medium" title={edge.target}>
              {edge.target}
            </span>
          </div>
        </div>
        <button
          onClick={onClose}
          className="p-1 hover:bg-slate-700 rounded-md text-slate-400 hover:text-white transition-colors"
          title="Close details"
        >
          <X className="w-5 h-5" />
        </button>
      </div>

      <div className="space-y-6">
        {/* Confidence */}
        <div>
          <div className="flex justify-between items-center mb-2">
            <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
              <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
              Confidence
            </h3>
            <span className="text-xs font-semibold text-slate-200">{Math.round(confidence * 100)}%</span>
          </div>
          <div className="h-2 w-full bg-slate-700 rounded-full overflow-hidden">
            <div className={`h-full ${confColor}`} style={{ width: `${confidence * 100}%` }} />
          </div>
        </div>

        {/* Reasoning */}
        {data.reason && (
          <div>
            <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">
              Reasoning
            </h3>
            <div className="bg-slate-900 p-3 rounded-lg border border-slate-700 text-sm text-slate-300 italic">
              "{data.reason}"
            </div>
          </div>
        )}

        {/* Underlying Cross-Entity Interactions */}
        {rawEdges.length > 0 && (
          <div>
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                <Layers className="w-3.5 h-3.5 text-brand-400" />
                Interactions ({rawEdges.length})
              </h3>
            </div>
            <div className="space-y-1.5 max-h-48 overflow-y-auto">
              {rawEdges.map((re, idx) => (
                <div
                  key={idx}
                  className="bg-slate-900/80 p-2 rounded border border-slate-700/60 text-xs font-mono flex items-center justify-between gap-2"
                >
                  <span className="text-slate-300 truncate" title={re.source}>{re.source}</span>
                  <span className="text-brand-400 text-[10px] font-sans uppercase px-1.5 py-0.5 rounded bg-slate-800 border border-slate-700 flex-shrink-0">
                    {re.relationship}
                  </span>
                  <span className="text-slate-300 truncate" title={re.target}>{re.target}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Evidence List */}
        {evidenceList.length > 0 && (
          <div>
            <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2 flex items-center gap-1.5">
              <FileSearch className="w-3.5 h-3.5 text-sky-400" />
              Evidence & References ({evidenceList.length})
            </h3>
            <div className="space-y-2 max-h-56 overflow-y-auto">
              {evidenceList.map((ev, idx) => (
                <div key={idx} className="bg-slate-900 p-3 rounded-lg border border-slate-700 text-xs">
                  {typeof ev === 'object' && ev !== null ? (
                    <table className="w-full text-left">
                      <tbody>
                        {Object.entries(ev).map(([k, v]) => (
                          <tr key={k} className="border-b border-slate-800 last:border-0">
                            <th className="py-1 pr-2 font-medium text-slate-400 uppercase tracking-wider text-[11px] w-1/3 align-top">
                              {k}
                            </th>
                            <td className="py-1 text-slate-200 font-mono break-all text-[11px]">
                              {typeof v === 'object' ? JSON.stringify(v) : String(v)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <div className="text-slate-300 font-mono">{String(ev)}</div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
