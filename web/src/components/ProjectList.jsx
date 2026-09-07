import React, { useState, useEffect } from 'react';
import { getProjects } from '../api/client';
import { Clock, Folder, ChevronRight, RefreshCw } from 'lucide-react';

export default function ProjectList({ onSelect }) {
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchProjects = async () => {
    try {
      const res = await getProjects();
      setProjects(res.data || []);
      setError(null);
    } catch (err) {
      setError('Failed to load projects');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchProjects();
    const interval = setInterval(fetchProjects, 30000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="bg-slate-800 rounded-xl border border-slate-700 p-6 shadow-xl h-full flex flex-col">
      <div className="flex justify-between items-center mb-6">
        <h2 className="text-xl font-bold flex items-center gap-2">
          <Clock className="text-brand-500" />
          Recent Projects
        </h2>
        <button 
          onClick={() => { setLoading(true); fetchProjects(); }}
          className="p-2 text-slate-400 hover:text-white hover:bg-slate-700 rounded-lg transition-colors"
          title="Refresh"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {error && <div className="text-red-400 text-sm mb-4">{error}</div>}

      <div className="flex-grow overflow-y-auto pr-2 space-y-3 custom-scrollbar">
        {loading && projects.length === 0 ? (
          <div className="text-center py-8 text-slate-500">Loading projects...</div>
        ) : projects.length === 0 ? (
          <div className="text-center py-8 text-slate-500 border border-dashed border-slate-700 rounded-lg">
            No previously analyzed projects
          </div>
        ) : (
          projects.map(project => (
            <div 
              key={project.id} 
              onClick={() => onSelect(project.id)}
              className="group bg-slate-900/50 hover:bg-slate-700 border border-slate-700 rounded-lg p-4 cursor-pointer transition-colors flex items-center justify-between"
            >
              <div className="overflow-hidden pr-4">
                <h3 className="font-semibold text-slate-200 truncate">{project.name || 'Unknown Project'}</h3>
                <div className="flex items-center gap-1 mt-1 text-xs text-slate-400">
                  <Folder className="w-3 h-3 flex-shrink-0" />
                  <span className="truncate">{project.path || 'Unknown Path'}</span>
                </div>
                <div className="mt-2 text-[10px] text-slate-500 uppercase tracking-wider">
                  {new Date(project.analyzedAt || project.created_at || Date.now()).toLocaleString()}
                </div>
              </div>
              <ChevronRight className="w-5 h-5 text-slate-500 group-hover:text-brand-400 transition-colors flex-shrink-0" />
            </div>
          ))
        )}
      </div>
    </div>
  );
}
