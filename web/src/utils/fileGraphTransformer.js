/**
 * LEVEL 1 — FILE-LEVEL REPOSITORY DEPENDENCY GRAPH TRANSFORMER
 *
 * Transforms entity-level AST dependency graphs and Code Map data into Level 1
 * file-level repository graphs:
 * 1. Each top-level node represents one source file (or an external package directly imported).
 * 2. Node label displays the actual filename (e.g. 'app.py', 'Login.js').
 * 3. Node ID uses the repository-relative path so files with identical names remain unique.
 * 4. Cross-file entity relationships (e.g. function calls across files) are converted to file-to-file edges.
 * 5. Self-relationships (entities within the same file) are omitted (no self-loops).
 * 6. 'contains' relationships are not used as file-to-file dependency edges.
 * 7. Multiple relationships between two files are grouped (e.g. 'imports + calls').
 * 8. External packages are represented only when directly imported/referenced by included application files.
 * 9. Header counts accurately reflect Level 1 file nodes and file-level dependency edges.
 * 10. High-performance O(1) in-memory mappings:
 *     - lookup.fileById
 *     - lookup.fileByPath
 *     - lookup.outgoingByFile
 *     - lookup.incomingByFile
 */

/**
 * Normalizes a path to a clean, repository-relative forward-slash path.
 */
export function normalizeFilePath(filePath, projectPath = '') {
  if (!filePath) return '';
  let cleaned = filePath.trim().replace(/^file:/i, '').replace(/\\+/g, '/');

  if (projectPath) {
    const normProj = projectPath.replace(/\\+/g, '/').replace(/\/+$/, '');
    if (cleaned.toLowerCase().startsWith(normProj.toLowerCase() + '/')) {
      cleaned = cleaned.slice(normProj.length + 1);
    } else if (cleaned.toLowerCase().startsWith(normProj.toLowerCase())) {
      cleaned = cleaned.slice(normProj.length);
    }
  }

  // Remove drive letters like C:/ or D:/
  cleaned = cleaned.replace(/^[A-Za-z]:\//, '');
  cleaned = cleaned.replace(/^\/+/, '');

  return cleaned;
}

/**
 * Extracts filename from path.
 */
export function getFilename(filePath) {
  if (!filePath) return '';
  const normalized = filePath.replace(/\\/g, '/');
  const parts = normalized.split('/');
  return parts[parts.length - 1] || filePath;
}

/**
 * Transforms raw graph data into a Level 1 File-Level Dependency Graph.
 *
 * @param {Object} rawGraph - { projectId, projectName, projectPath, nodes, edges, codeMap }
 * @returns {Object} { projectId, projectName, projectPath, nodes, edges, fileCount, depCount, packageCount, lookup }
 */
export function transformToFileLevelGraph(rawGraph) {
  if (!rawGraph) {
    return {
      projectId: '',
      projectName: '',
      projectPath: '',
      nodes: [],
      edges: [],
      fileCount: 0,
      depCount: 0,
      packageCount: 0,
      lookup: {
        fileById: new Map(),
        fileByPath: new Map(),
        outgoingByFile: new Map(),
        incomingByFile: new Map()
      }
    };
  }

  const projectPath = rawGraph.projectPath || '';
  const rawNodes = Array.isArray(rawGraph.nodes) ? rawGraph.nodes : [];
  const rawEdges = Array.isArray(rawGraph.edges) ? rawGraph.edges : [];
  const codeMap = rawGraph.codeMap || {};
  const rawCodeMapFiles = codeMap.files;
  let codeMapFiles = [];
  if (Array.isArray(rawCodeMapFiles)) {
    codeMapFiles = rawCodeMapFiles;
  } else if (rawCodeMapFiles && typeof rawCodeMapFiles === 'object') {
    codeMapFiles = Object.entries(rawCodeMapFiles).map(([key, val]) => {
      const rel = val.relativePath || val.file?.relativePath || key;
      return {
        ...val,
        relativePath: rel,
        name: val.name || val.file?.name || getFilename(rel)
      };
    });
  }
  const codeMapRelationships = Array.isArray(codeMap.relationships) ? codeMap.relationships : [];

  const cleanPath = (p) => {
    return normalizeFilePath(p, projectPath);
  };

  // Maps for resolution and O(1) lookups
  const fileNodesMap = new Map(); // fileId (relative path) -> Level 1 node
  const fileDetailsById = new Map(); // fileId -> complete Code Map file artifact
  const fileDetailsByPath = new Map(); // normalized path -> complete Code Map file artifact
  const entityToFileMap = new Map(); // entityNodeId or symbol/call string -> fileId or pkgId
  const externalPackagesMap = new Map(); // pkgId -> package node

  // 1. Index Code Map files (the source of truth for parsed application files)
  for (const fileArtifact of codeMapFiles) {
    const fileInfo = fileArtifact.file || {};
    const rawRel = fileArtifact.relativePath || fileInfo.relativePath || fileInfo.path || fileArtifact.path;
    const relPath = cleanPath(rawRel);
    if (!relPath) continue;

    const fileName = fileArtifact.name || fileInfo.name || getFilename(relPath);
    const fileId = relPath; // Guaranteed unique by relative path

    // Save into O(1) lookup tables
    fileDetailsById.set(fileId, fileArtifact);
    fileDetailsByPath.set(relPath, fileArtifact);
    if (fileInfo.path || fileArtifact.path) {
      const absP = fileInfo.path || fileArtifact.path;
      fileDetailsByPath.set(cleanPath(absP), fileArtifact);
    }

    // Map file path variants to fileId
    entityToFileMap.set(fileId, fileId);
    entityToFileMap.set(`file:${fileId}`, fileId);
    if (fileInfo.path || fileArtifact.path) {
      const absP = fileInfo.path || fileArtifact.path;
      entityToFileMap.set(absP, fileId);
      entityToFileMap.set(cleanPath(absP), fileId);
      entityToFileMap.set(`file:${cleanPath(absP)}`, fileId);
    }

    // Index all symbols, classes, functions, components declared in this file
    for (const sym of fileArtifact.symbols || []) {
      if (sym.name) {
        entityToFileMap.set(sym.name, fileId);
        entityToFileMap.set(`${fileId}#${sym.name}`, fileId);
        entityToFileMap.set(`${sym.type || 'symbol'}:${sym.name}`, fileId);
      }
    }
    for (const decl of fileArtifact.declarations || []) {
      if (decl.name) {
        entityToFileMap.set(decl.name, fileId);
        entityToFileMap.set(`${fileId}#${decl.name}`, fileId);
        entityToFileMap.set(`${decl.type || 'symbol'}:${decl.name}`, fileId);
      }
    }
    for (const cls of fileArtifact.classes || []) {
      if (cls.name) {
        entityToFileMap.set(cls.name, fileId);
        entityToFileMap.set(`class:${cls.name}`, fileId);
        entityToFileMap.set(`${fileId}#${cls.name}`, fileId);
      }
    }
    for (const comp of fileArtifact.react?.components || fileArtifact.components || []) {
      const compName = typeof comp === 'string' ? comp : comp.name;
      if (compName) {
        entityToFileMap.set(compName, fileId);
        entityToFileMap.set(`component:${compName}`, fileId);
      }
    }

    // Register any direct external dependencies as package candidates
    for (const dep of fileArtifact.dependencies || []) {
      const target = dep.target || dep.source;
      if (target && (dep.type === 'external' || (!target.startsWith('.') && !target.startsWith('/')))) {
        const pkgName = target;
        const pkgId = `pkg:${pkgName}`;
        if (!externalPackagesMap.has(pkgId)) {
          externalPackagesMap.set(pkgId, {
            id: pkgId,
            type: 'package',
            name: pkgName,
            label: pkgName,
            filePath: null,
            isExternal: true,
            metadata: { external: true }
          });
        }
        entityToFileMap.set(pkgName, pkgId);
        entityToFileMap.set(pkgId, pkgId);
      }
    }

    // Initialize Level 1 Node
    fileNodesMap.set(fileId, {
      id: fileId,
      type: 'file',
      name: fileName,
      label: fileName,
      filePath: relPath,
      language: fileArtifact.language || fileInfo.language || null,
      lineCount: fileArtifact.lineCount !== undefined ? fileArtifact.lineCount : (fileInfo.lineCount || 0),
      parseStatus: fileArtifact.parseStatus || fileArtifact.parse?.status || 'success',
      artifact: fileArtifact
    });
  }

  // 2. Process backend rawNodes
  for (const node of rawNodes) {
    const isExternal =
      node.type === 'module' ||
      node.type === 'package' ||
      node.id.startsWith('module:') ||
      node.id.startsWith('package:') ||
      (node.metadata && node.metadata.external === true);

    if (isExternal) {
      const pkgName = node.name || node.id.replace(/^(module|package):/, '');
      const pkgId = `pkg:${pkgName}`;
      if (!externalPackagesMap.has(pkgId)) {
        externalPackagesMap.set(pkgId, {
          id: pkgId,
          type: 'package',
          name: pkgName,
          label: pkgName,
          filePath: null,
          isExternal: true,
          metadata: { ...(node.metadata || {}), external: true }
        });
      }
      entityToFileMap.set(node.id, pkgId);
      entityToFileMap.set(pkgName, pkgId);
      continue;
    }

    // If node is a file node from rawNodes that wasn't in codeMapFiles
    if (node.type === 'file' || node.id.startsWith('file:')) {
      const rawP = node.filePath || node.id.replace(/^file:/i, '');
      const relPath = cleanPath(rawP);
      const fileName = node.name || getFilename(relPath);
      const fileId = relPath || fileName;

      if (!fileNodesMap.has(fileId)) {
        const fallbackArtifact = {
          file: {
            relativePath: relPath,
            name: fileName,
            language: node.metadata?.language || null,
            lineCount: node.metadata?.lineCount || 0
          },
          parse: { status: 'success' },
          symbols: node.metadata?.symbols || [],
          classes: node.metadata?.classes || [],
          imports: [],
          exports: [],
          calls: [],
          routes: node.metadata?.routes || [],
          databaseOperations: node.metadata?.databaseOperations || []
        };

        fileNodesMap.set(fileId, {
          id: fileId,
          type: 'file',
          name: fileName,
          label: fileName,
          filePath: relPath,
          language: node.metadata?.language || null,
          lineCount: node.metadata?.lineCount || 0,
          parseStatus: 'success',
          artifact: fallbackArtifact
        });
        fileDetailsById.set(fileId, fallbackArtifact);
        fileDetailsByPath.set(relPath, fallbackArtifact);
      }
      entityToFileMap.set(node.id, fileId);
      if (node.filePath) {
        entityToFileMap.set(node.filePath, fileId);
        entityToFileMap.set(cleanPath(node.filePath), fileId);
      }
      continue;
    }

    // If node is an entity (function, class, component, route, db_op), map to file via node.filePath
    if (node.filePath) {
      const rel = cleanPath(node.filePath);
      if (fileNodesMap.has(rel)) {
        entityToFileMap.set(node.id, rel);
      } else {
        const fn = getFilename(rel);
        for (const fId of fileNodesMap.keys()) {
          if (fId === fn || fId.endsWith('/' + fn)) {
            entityToFileMap.set(node.id, fId);
            break;
          }
        }
      }
    }
  }

  // 3. Resolve Cross-Entity Relationships into File-to-File Edges
  const combinedEdges = [...rawEdges];
  for (const rel of codeMapRelationships) {
    combinedEdges.push({
      source: rel.from,
      target: rel.to,
      relationship: rel.type || 'imports',
      evidence: rel.evidence || {},
      confidence: 1.0,
      reason: 'Static code analysis evidence'
    });
  }

  const fileEdgesMap = new Map(); // `${sourceFile}->${targetFile}` -> edge
  const referencedExternalPackages = new Set();

  for (const edge of combinedEdges) {
    // Exclude 'contains' relationships from file-level graph
    if (edge.relationship === 'contains') continue;

    let sourceFile = entityToFileMap.get(edge.source) || cleanPath(edge.source);
    let targetFile = entityToFileMap.get(edge.target) || cleanPath(edge.target);

    // Fallback resolution if not directly in entityToFileMap
    if (!fileNodesMap.has(sourceFile)) {
      const srcFn = getFilename(sourceFile);
      for (const fId of fileNodesMap.keys()) {
        if (fId === srcFn || fId.endsWith('/' + srcFn)) {
          sourceFile = fId;
          break;
        }
      }
    }

    if (!fileNodesMap.has(targetFile) && !externalPackagesMap.has(targetFile)) {
      if (externalPackagesMap.has(`pkg:${targetFile}`)) {
        targetFile = `pkg:${targetFile}`;
      } else {
        const tgtFn = getFilename(targetFile);
        for (const fId of fileNodesMap.keys()) {
          if (fId === tgtFn || fId.endsWith('/' + tgtFn)) {
            targetFile = fId;
            break;
          }
        }
      }
    }

    // Validate that source and target exist
    if (!fileNodesMap.has(sourceFile)) continue;
    if (!fileNodesMap.has(targetFile) && !externalPackagesMap.has(targetFile)) continue;

    // Do NOT create self-edges where source and target resolve to the same file
    if (sourceFile === targetFile) continue;

    if (externalPackagesMap.has(targetFile)) {
      referencedExternalPackages.add(targetFile);
    }

    const edgeKey = `${sourceFile}->${targetFile}`;
    const relType = edge.relationship || 'imports';

    if (!fileEdgesMap.has(edgeKey)) {
      fileEdgesMap.set(edgeKey, {
        id: `edge:${edgeKey}`,
        source: sourceFile,
        target: targetFile,
        relationships: [relType],
        confidence: edge.confidence !== undefined ? edge.confidence : 1.0,
        evidenceList: edge.evidence && Object.keys(edge.evidence).length > 0 ? [edge.evidence] : [],
        reasons: edge.reason ? [edge.reason] : [],
        rawEdges: [edge]
      });
    } else {
      const existing = fileEdgesMap.get(edgeKey);
      if (!existing.relationships.includes(relType)) {
        existing.relationships.push(relType);
      }
      if (edge.evidence && Object.keys(edge.evidence).length > 0) {
        existing.evidenceList.push(edge.evidence);
      }
      if (edge.reason && !existing.reasons.includes(edge.reason)) {
        existing.reasons.push(edge.reason);
      }
      existing.rawEdges.push(edge);
      existing.confidence = Math.max(existing.confidence, edge.confidence !== undefined ? edge.confidence : 1.0);
    }
  }

  // 4. Final Edges & O(1) Dependency Lookup Tables (Requirement 11 & 17)
  const outgoingByFile = new Map(); // fileId -> Array of outgoing dependencies
  const incomingByFile = new Map(); // fileId -> Array of incoming dependencies

  const finalEdges = Array.from(fileEdgesMap.values()).map(e => {
    const relLabel = e.relationships.join(' + ');
    const edgeObj = {
      id: e.id,
      source: e.source,
      target: e.target,
      relationship: relLabel,
      relationships: e.relationships,
      confidence: e.confidence,
      evidence: e.evidenceList.length === 1 ? e.evidenceList[0] : { occurrences: e.evidenceList },
      evidenceList: e.evidenceList,
      reason: e.reasons.join('; '),
      rawEdges: e.rawEdges
    };

    // Populate outgoing
    if (!outgoingByFile.has(e.source)) outgoingByFile.set(e.source, []);
    const targetNode = fileNodesMap.get(e.target) || externalPackagesMap.get(e.target);
    outgoingByFile.get(e.source).push({
      targetId: e.target,
      targetName: targetNode?.name || e.target,
      targetPath: targetNode?.filePath || null,
      isExternal: Boolean(targetNode?.isExternal),
      relationships: e.relationships,
      relationship: relLabel,
      confidence: e.confidence,
      evidenceList: e.evidenceList,
      reasons: e.reasons
    });

    // Populate incoming
    if (!incomingByFile.has(e.target)) incomingByFile.set(e.target, []);
    const sourceNode = fileNodesMap.get(e.source);
    incomingByFile.get(e.target).push({
      sourceId: e.source,
      sourceName: sourceNode?.name || e.source,
      sourcePath: sourceNode?.filePath || null,
      relationships: e.relationships,
      relationship: relLabel,
      confidence: e.confidence,
      evidenceList: e.evidenceList,
      reasons: e.reasons
    });

    return edgeObj;
  });

  // Only include external packages that are actually referenced
  const activePackages = Array.from(externalPackagesMap.values()).filter(pkg =>
    referencedExternalPackages.has(pkg.id)
  );

  const finalNodes = [
    ...Array.from(fileNodesMap.values()),
    ...activePackages
  ];

  return {
    projectId: rawGraph.projectId || '',
    projectName: rawGraph.projectName || '',
    projectPath: rawGraph.projectPath || '',
    nodes: finalNodes,
    edges: finalEdges,
    fileCount: fileNodesMap.size,
    depCount: finalEdges.length,
    packageCount: activePackages.length,
    lookup: {
      fileById: fileDetailsById,
      fileByPath: fileDetailsByPath,
      outgoingByFile,
      incomingByFile
    }
  };
}
