import React, { useCallback, useMemo, useEffect } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  MarkerType,
  Handle,
  Position
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import dagre from 'dagre';
import { FileCode, Package } from 'lucide-react';
import { transformToFileLevelGraph } from '../utils/fileGraphTransformer';

// Custom Node for Level-1 File Nodes
function FileNodeComponent({ data, selected }) {
  const isExternal = data.isExternal || data.nodeType === 'package' || data.nodeType === 'module';
  const lang = data.language || '';
  const dirPath = data.filePath ? data.filePath.split('/').slice(0, -1).join('/') : '';

  return (
    <div
      className={`relative px-4 py-2.5 rounded-xl border transition-all duration-200 cursor-pointer min-w-[170px] max-w-[240px] shadow-lg ${
        selected
          ? 'ring-2 ring-brand-400 border-brand-300 bg-slate-900 shadow-brand-500/25'
          : isExternal
          ? 'bg-slate-900/95 border-indigo-500/50 hover:border-indigo-400'
          : 'bg-slate-900/95 border-slate-700 hover:border-sky-500 hover:shadow-sky-500/10'
      }`}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="w-2.5 h-2.5 !bg-sky-400 border-2 border-slate-900"
      />

      <div className="flex items-center justify-between gap-2 mb-1">
        <div className="flex items-center gap-1.5 min-w-0">
          {isExternal ? (
            <Package className="w-4 h-4 text-indigo-400 flex-shrink-0" />
          ) : (
            <FileCode className="w-4 h-4 text-sky-400 flex-shrink-0" />
          )}
          <span className="font-semibold text-sm text-slate-100 truncate" title={data.label}>
            {data.label}
          </span>
        </div>
        {lang && !isExternal && (
          <span className="text-[10px] font-mono uppercase px-1.5 py-0.2 rounded bg-sky-950 text-sky-300 border border-sky-800/50 flex-shrink-0">
            {lang}
          </span>
        )}
      </div>

      {dirPath ? (
        <p className="text-[11px] text-slate-400 truncate font-mono" title={data.filePath}>
          {dirPath}/
        </p>
      ) : isExternal ? (
        <p className="text-[11px] text-indigo-400 font-mono">
          external package
        </p>
      ) : null}

      <Handle
        type="source"
        position={Position.Right}
        className="w-2.5 h-2.5 !bg-sky-400 border-2 border-slate-900"
      />
    </div>
  );
}

const nodeTypes = {
  fileNode: FileNodeComponent
};

const getLayoutedElements = (nodes, edges) => {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'LR', nodesep: 60, ranksep: 120 });

  nodes.forEach(n => g.setNode(n.id, { width: 220, height: 64 }));
  edges.forEach(e => g.setEdge(e.source, e.target));

  dagre.layout(g);

  return {
    nodes: nodes.map(n => {
      const pos = g.node(n.id) || { x: 100, y: 100 };
      return {
        ...n,
        position: { x: pos.x - 110, y: pos.y - 32 }
      };
    }),
    edges
  };
};

const toFlowNodes = (rawNodes, selectedNodeId) => rawNodes.map(n => ({
  id: n.id.toString(),
  type: 'fileNode',
  selected: n.id.toString() === selectedNodeId,
  data: {
    label: n.name || n.label,
    nodeType: n.type,
    filePath: n.filePath,
    language: n.language,
    lineCount: n.lineCount,
    parseStatus: n.parseStatus,
    metadata: n.metadata,
    location: n.location,
    isExternal: n.isExternal,
    artifact: n.artifact
  }
}));

const toFlowEdges = (rawEdges, selectedEdgeId) => rawEdges.map(e => {
  const isSelected = e.id === selectedEdgeId;
  return {
    id: e.id ? e.id.toString() : `${e.source}->${e.target}`,
    source: e.source.toString(),
    target: e.target.toString(),
    type: 'smoothstep',
    label: e.relationship,
    selected: isSelected,
    data: {
      relationship: e.relationship,
      relationships: e.relationships || [e.relationship],
      confidence: e.confidence !== undefined ? e.confidence : 1.0,
      evidence: e.evidence,
      evidenceList: e.evidenceList || [],
      reason: e.reason,
      rawEdges: e.rawEdges || []
    },
    markerEnd: {
      type: MarkerType.ArrowClosed,
      color: isSelected ? '#38bdf8' : '#38bdf8',
      width: 18,
      height: 18
    },
    style: {
      stroke: isSelected ? '#38bdf8' : '#0284c7',
      strokeWidth: isSelected ? 3 : 2
    },
    labelStyle: { fill: '#cbd5e1', fontSize: 11, fontWeight: '600' },
    labelBgStyle: { fill: '#0f172a', fillOpacity: 0.95, rx: 4, ry: 4 },
    labelBgPadding: [6, 4]
  };
});

export default function GraphView({ graphData, onNodeClick, onEdgeClick, selectedNodeId, selectedEdgeId }) {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  // Transform raw graph data & Code Map into Level 1 File-Level graph with O(1) lookups
  const level1Graph = useMemo(() => {
    return transformToFileLevelGraph(graphData);
  }, [graphData]);

  useEffect(() => {
    if (!level1Graph) return;
    const initialNodes = toFlowNodes(level1Graph.nodes || [], selectedNodeId);
    const initialEdges = toFlowEdges(level1Graph.edges || [], selectedEdgeId);

    const layouted = getLayoutedElements(initialNodes, initialEdges);
    setNodes(layouted.nodes);
    setEdges(layouted.edges);
  }, [level1Graph, selectedNodeId, selectedEdgeId, setNodes, setEdges]);

  const handleNodeClick = useCallback((_, node) => {
    if (onNodeClick) onNodeClick(node, level1Graph.lookup);
  }, [onNodeClick, level1Graph]);

  const handleEdgeClick = useCallback((_, edge) => {
    if (onEdgeClick) onEdgeClick(edge);
  }, [onEdgeClick]);

  return (
    <div className="w-full h-full relative">
      {/* Dynamic Header Statistics Banner */}
      <div className="absolute top-4 left-4 z-10 bg-slate-800/90 backdrop-blur border border-slate-700 px-4 py-2.5 rounded-lg text-sm shadow-xl flex items-center">
        <span className="font-bold text-brand-400">{level1Graph?.projectName || 'Project'}</span>
        <span className="mx-2 text-slate-500">·</span>
        <span className="text-slate-200 font-medium">
          {level1Graph.fileCount} {level1Graph.fileCount === 1 ? 'file' : 'files'}
        </span>
        <span className="mx-2 text-slate-500">·</span>
        <span className="text-slate-200 font-medium">
          {level1Graph.depCount} {level1Graph.depCount === 1 ? 'dependency' : 'dependencies'}
        </span>
        {level1Graph.packageCount > 0 && (
          <>
            <span className="mx-2 text-slate-500">·</span>
            <span className="text-indigo-300 font-medium">
              {level1Graph.packageCount} external {level1Graph.packageCount === 1 ? 'package' : 'packages'}
            </span>
          </>
        )}
      </div>

      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={handleNodeClick}
        onEdgeClick={handleEdgeClick}
        fitView
        attributionPosition="bottom-right"
      >
        <Background color="#334155" gap={16} />
        <Controls />
        <MiniMap
          nodeColor={(n) => n.data?.isExternal ? '#4f46e5' : '#0284c7'}
          maskColor="rgba(15, 23, 42, 0.7)"
        />
      </ReactFlow>
    </div>
  );
}
