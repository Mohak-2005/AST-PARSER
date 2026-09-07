import React from 'react';
import { CheckCircle, Loader2, Circle } from 'lucide-react';

const STAGES = [
  'Initializing',
  'Scanning project files',
  'Parsing source files',
  'Generating JSON artifacts',
  'Building repository Code Map',
  'Sending to backend',
  'Calling LLM for graph generation',
  'Validating dependency graph',
  'Saving to database',
  'Complete'
];

export default function ProgressPanel({ status }) {
  const currentStageName = status?.stage || 'Initializing';
  const currentIndex = STAGES.indexOf(currentStageName);
  
  return (
    <div className="max-w-2xl mx-auto bg-slate-800 rounded-xl border border-slate-700 p-8 shadow-xl mt-8">
      <h2 className="text-2xl font-bold mb-6 text-center">Analysis in Progress</h2>
      
      <div className="space-y-4">
        {STAGES.map((stage, idx) => {
          const isDone = currentIndex > idx || (currentStageName === 'Complete');
          const isCurrent = currentIndex === idx && currentStageName !== 'Complete' && currentStageName !== 'Error';
          const isPending = currentIndex < idx && currentStageName !== 'Error';
          
          return (
            <div key={stage} className={`flex items-center gap-4 p-3 rounded-lg ${isCurrent ? 'bg-slate-700/50 border border-slate-600' : ''}`}>
              <div className="flex-shrink-0">
                {isDone ? (
                  <CheckCircle className="w-6 h-6 text-emerald-500" />
                ) : isCurrent ? (
                  <Loader2 className="w-6 h-6 text-brand-500 animate-spin" />
                ) : (
                  <Circle className="w-6 h-6 text-slate-600" />
                )}
              </div>
              <div className="flex-grow">
                <p className={`font-medium ${isCurrent ? 'text-brand-400' : isDone ? 'text-slate-300' : 'text-slate-500'}`}>
                  {stage}
                </p>
                {isCurrent && status?.progress && typeof status.progress === 'object' && (
                  <p className="text-sm text-slate-400 mt-1">
                    {status.progress.current} / {status.progress.total} files
                  </p>
                )}
              </div>
            </div>
          );
        })}
        {currentStageName === 'Error' && (
          <div className="flex items-center gap-4 p-3 rounded-lg bg-red-900/20 border border-red-500/30">
            <div className="flex-shrink-0">
              <Circle className="w-6 h-6 text-red-500" />
            </div>
            <div className="flex-grow">
              <p className="font-medium text-red-400">Analysis Failed</p>
              <p className="text-sm text-red-300 mt-1">{status?.error || 'An error occurred'}</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
