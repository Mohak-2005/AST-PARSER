const fs = require('fs');
const path = require('path');

/**
 * Builds the repository-level Code Map from per-file AST artifacts.
 * Resolves cross-file dependencies dynamically across Java, Python, C/C++, JS/TS, etc.
 */
function buildCodeMap(projectInfo, allArtifacts, scanStats = {}) {
  // Collect detected languages dynamically
  const detectedLanguages = new Set();
  allArtifacts.forEach(a => {
    if (a.file && a.file.language) {
      detectedLanguages.add(a.file.language);
    }
  });

  const parsedFilesCount = allArtifacts.filter(a => a.parse && a.parse.status === 'success').length;
  const failedFilesCount = allArtifacts.filter(a => a.parse && a.parse.status === 'error').length;

  const stats = {
    totalFilesDiscovered: scanStats.totalFilesDiscovered !== undefined ? scanStats.totalFilesDiscovered : allArtifacts.length,
    analyzableSourceFiles: allArtifacts.length,
    supportedFiles: allArtifacts.length,
    unsupportedFiles: scanStats.unsupportedFiles !== undefined ? scanStats.unsupportedFiles : 0,
    successfullyParsedFiles: parsedFilesCount,
    failedParses: failedFilesCount,
    languagesDetected: Array.from(detectedLanguages),
    directoriesSkippedByReason: scanStats.directoriesSkippedByReason || {},
    filesSkippedByReason: scanStats.filesSkippedByReason || {}
  };

  const codeMap = {
    project: {
      name: projectInfo.name,
      path: projectInfo.path,
      analyzedAt: new Date().toISOString(),
      totalFiles: stats.totalFilesDiscovered,
      analyzableSourceFiles: stats.supportedFiles,
      supportedFiles: stats.supportedFiles,
      unsupportedFiles: stats.unsupportedFiles,
      parsedFiles: stats.successfullyParsedFiles,
      failedFiles: stats.failedParses,
      languagesDetected: stats.languagesDetected
    },
    stats,
    unsupportedFiles: scanStats.unsupportedFileList || [],
    files: {},
    relationships: []
  };

  // 1. Index files by normalized relative path and basename
  const fileMap = new Map(); // 'src/utils.js' -> artifact
  const baseNameToFileMap = new Map(); // 'utils.js' -> artifact
  const classToFileMap = new Map(); // 'Card' -> artifact

  allArtifacts.forEach(artifact => {
    const rel = (artifact.file.relativePath || '').replace(/\\/g, '/');
    fileMap.set(rel, artifact);

    const base = path.basename(rel);
    if (!baseNameToFileMap.has(base)) {
      baseNameToFileMap.set(base, artifact);
    }

    // Index all classes defined in this artifact
    (artifact.classes || []).forEach(cls => {
      if (cls.name && !classToFileMap.has(cls.name)) {
        classToFileMap.set(cls.name, artifact);
      }
    });
  });

  // 2. Resolve cross-file dependencies
  const seenEdges = new Set();

  function addRelationship(from, to, type, evidence, external = false) {
    if (!from || !to) return;
    const key = `${from}->${to}:${type}`;
    if (seenEdges.has(key)) return;
    seenEdges.add(key);

    codeMap.relationships.push({
      from,
      to,
      type,
      evidence: evidence || {},
      external
    });
  }

  allArtifacts.forEach(artifact => {
    const fromPath = (artifact.file.relativePath || '').replace(/\\/g, '/');
    const fromDir = path.dirname(fromPath);
    const lang = artifact.file.language || '';

    // A. Explicit Imports / Includes / Usings
    (artifact.imports || []).forEach(imp => {
      const source = imp.source;
      if (!source) return;

      const line = imp.location ? imp.location.startLine : null;
      const rawStmt = imp.rawStatement || `import ${source}`;

      // 1. C/C++ #include resolution
      if (lang === 'c' || lang === 'cpp') {
        const isSystem = imp.specifiers && imp.specifiers.some(s => s.type === 'system_include');
        if (isSystem) {
          addRelationship(fromPath, source, '#include', { statement: rawStmt, line, system: true }, true);
        } else {
          // Look for local header file
          let targetRel = null;
          const candidateRel = path.posix.join(fromDir, source);
          if (fileMap.has(candidateRel)) {
            targetRel = candidateRel;
          } else if (fileMap.has(source)) {
            targetRel = source;
          } else if (baseNameToFileMap.has(path.basename(source))) {
            targetRel = baseNameToFileMap.get(path.basename(source)).file.relativePath.replace(/\\/g, '/');
          }

          addRelationship(
            fromPath,
            targetRel || source,
            '#include',
            { statement: rawStmt, line },
            !targetRel
          );
        }
        return;
      }

      // 2. Java Import resolution
      if (lang === 'java') {
        // e.g. import com.example.Card; or import java.util.*;
        const isStdLib = /^(java|javax|sun|com\.sun)\./.test(source);
        if (isStdLib) {
          addRelationship(fromPath, source, 'imports', { statement: rawStmt, line, library: 'java_standard' }, true);
        } else {
          const className = source.split('.').pop();
          let targetRel = null;

          // Check if class exists in project
          if (className && className !== '*' && classToFileMap.has(className)) {
            targetRel = classToFileMap.get(className).file.relativePath.replace(/\\/g, '/');
          } else {
            // Try path matching: com.example.Foo -> com/example/Foo.java
            const asPath = source.replace(/\./g, '/') + '.java';
            if (fileMap.has(asPath)) {
              targetRel = asPath;
            } else if (baseNameToFileMap.has(className + '.java')) {
              targetRel = baseNameToFileMap.get(className + '.java').file.relativePath.replace(/\\/g, '/');
            }
          }

          addRelationship(
            fromPath,
            targetRel || source,
            'imports',
            { statement: rawStmt, line },
            !targetRel
          );
        }
        return;
      }

      // 3. JavaScript / TypeScript / Python / Relative Imports
      const isRelative = source.startsWith('.');
      let resolvedTo = null;

      if (isRelative) {
        const potentialTarget = path.posix.join(fromDir, source);
        const extensions = ['', '.js', '.jsx', '.ts', '.tsx', '.py', '/index.js', '/index.ts', '/index.jsx', '/index.tsx', '/__init__.py'];
        for (const ext of extensions) {
          const tryPath = potentialTarget + ext;
          if (fileMap.has(tryPath)) {
            resolvedTo = tryPath;
            break;
          }
        }
      } else {
        // Python module in same project or subpackage
        if (lang === 'python') {
          const tryPy = source.replace(/\./g, '/') + '.py';
          const tryInit = source.replace(/\./g, '/') + '/__init__.py';
          if (fileMap.has(tryPy)) {
            resolvedTo = tryPy;
          } else if (fileMap.has(tryInit)) {
            resolvedTo = tryInit;
          } else if (baseNameToFileMap.has(source + '.py')) {
            resolvedTo = baseNameToFileMap.get(source + '.py').file.relativePath.replace(/\\/g, '/');
          }
        }
      }

      addRelationship(
        fromPath,
        resolvedTo || source,
        'imports',
        { importPath: source, line },
        !resolvedTo
      );
    });

    // B. Java Instantiations & Cross-File Class References (e.g. new Card() or Deck deck)
    if (lang === 'java') {
      (artifact.calls || []).forEach(call => {
        if (call.kind === 'instantiation' && call.targetClass) {
          const targetClass = call.targetClass;
          if (classToFileMap.has(targetClass)) {
            const targetRel = classToFileMap.get(targetClass).file.relativePath.replace(/\\/g, '/');
            if (targetRel !== fromPath) {
              addRelationship(
                fromPath,
                targetRel,
                'instantiates',
                { class: targetClass, line: call.location ? call.location.startLine : null },
                false
              );
            }
          }
        }
      });

      // Check class inheritance (extends / implements)
      (artifact.classes || []).forEach(cls => {
        if (cls.extends && classToFileMap.has(cls.extends)) {
          const targetRel = classToFileMap.get(cls.extends).file.relativePath.replace(/\\/g, '/');
          if (targetRel !== fromPath) {
            addRelationship(fromPath, targetRel, 'extends', { class: cls.name, superclass: cls.extends, line: cls.location ? cls.location.startLine : null }, false);
          }
        }
        (cls.implements || []).forEach(iface => {
          if (classToFileMap.has(iface)) {
            const targetRel = classToFileMap.get(iface).file.relativePath.replace(/\\/g, '/');
            if (targetRel !== fromPath) {
              addRelationship(fromPath, targetRel, 'implements', { class: cls.name, interface: iface, line: cls.location ? cls.location.startLine : null }, false);
            }
          }
        });
      });
    }
  });

  // 3. Convert allArtifacts into streamlined dictionary keyed by relativePath
  const filesMap = {};
  allArtifacts.forEach(artifact => {
    const rel = (artifact.file?.relativePath || artifact.relativePath || '').replace(/\\/g, '/');
    if (rel) {
      filesMap[rel] = toStreamlinedFileEntry(artifact);
    }
  });
  codeMap.files = filesMap;

  return codeMap;
}

/**
 * Transforms an AST artifact into an ultra-lean file entry for the consolidated Code Map:
 * - Omits duplicate nested 'file', 'parse', 'imports', 'symbols', 'classes' structures
 * - Retains ONLY what is required for dependency analysis:
 *   - language: string
 *   - lineCount: number
 *   - dependencies: [{ target, type, kind, line }] (if present)
 *   - exports: [{ name, type, line }] (if present)
 *   - declarations: [{ name, type, line, extends?, implements?, methods? }] (if present)
 *   - routes: [{ method, path, line }] (if present)
 *   - databaseOperations: [{ operation, line }] (if present)
 * - Excludes built-in/local calls (Math.*, console.*, this.*)
 */
function toStreamlinedFileEntry(artifact) {
  if (!artifact) return null;

  const file = artifact.file || {};
  const relPath = (artifact.relativePath || file.relativePath || '').replace(/\\/g, '/');
  const lang = artifact.language || file.language || '';
  const lineCount = artifact.lineCount !== undefined ? artifact.lineCount : (file.lineCount || 0);

  const entry = {
    language: lang,
    lineCount: lineCount
  };

  // If parse errors exist, record them concisely
  const parseErrors = artifact.parseErrors || (artifact.parse?.status === 'error' ? artifact.parse.errors : []);
  if (parseErrors && parseErrors.length > 0) {
    entry.parseErrors = parseErrors;
  }

  // 1. Dependencies (imports, require, includes)
  const dependencies = [];
  const rawImports = artifact.dependencies || artifact.imports || [];
  rawImports.forEach(imp => {
    const src = imp.target || imp.source || imp.modulePath;
    if (!src) return;

    const isRelative = src.startsWith('.');
    const kind = imp.kind || imp.importType || (src.startsWith('#include') ? 'include' : (src.startsWith('.') ? 'import' : 'import'));
    const line = imp.line || imp.location?.startLine || null;

    const dep = {
      target: src,
      type: imp.type || (isRelative ? 'internal' : 'external'),
      kind: kind
    };
    if (line) dep.line = line;
    dependencies.push(dep);
  });

  if (dependencies.length > 0) {
    entry.dependencies = dependencies;
  }

  // 2. Exports
  if (artifact.exports && artifact.exports.length > 0) {
    entry.exports = artifact.exports.map(exp => {
      const e = {
        name: exp.name || '(default)',
        type: exp.type || exp.exportType || 'named'
      };
      const l = exp.line || exp.location?.startLine;
      if (l) e.line = l;
      return e;
    });
  }

  // 3. Declarations (Classes, Functions, Components, Types, Models)
  const declarations = [];
  if (artifact.declarations && Array.isArray(artifact.declarations)) {
    declarations.push(...artifact.declarations);
  } else {
    // Classes
    (artifact.classes || []).forEach(cls => {
      if (cls.name) {
        const decl = {
          name: cls.name,
          type: 'class'
        };
        const l = cls.line || cls.location?.startLine;
        if (l) decl.line = l;
        if (cls.extends) decl.extends = cls.extends;
        if (cls.implements && cls.implements.length > 0) decl.implements = cls.implements;
        if (cls.methods && cls.methods.length > 0) {
          decl.methods = cls.methods.map(m => typeof m === 'string' ? m : m.name).filter(Boolean);
        }
        declarations.push(decl);
      }
    });

    // Symbols (functions, methods, interfaces)
    (artifact.symbols || []).forEach(sym => {
      if (sym.name && sym.type !== 'class') {
        const decl = {
          name: sym.name,
          type: sym.type || 'symbol'
        };
        const l = sym.line || sym.location?.startLine;
        if (l) decl.line = l;
        declarations.push(decl);
      }
    });

    // Components (React/Vue/etc.)
    (artifact.react?.components || []).forEach(comp => {
      const compName = typeof comp === 'string' ? comp : comp.name;
      if (compName && !declarations.some(d => d.name === compName)) {
        const decl = {
          name: compName,
          type: 'component'
        };
        const l = comp.line || comp.location?.startLine;
        if (l) decl.line = l;
        declarations.push(decl);
      }
    });
  }

  if (declarations.length > 0) {
    entry.declarations = declarations;
  }

  // 4. Routes (only if present)
  if (artifact.routes && artifact.routes.length > 0) {
    entry.routes = artifact.routes.map(r => {
      const routeObj = {
        method: r.method || 'ROUTE',
        path: r.path
      };
      const l = r.line || r.location?.startLine;
      if (l) routeObj.line = l;
      return routeObj;
    });
  }

  // 5. Database operations (only if present)
  if (artifact.databaseOperations && artifact.databaseOperations.length > 0) {
    entry.databaseOperations = artifact.databaseOperations.map(d => {
      const dbObj = {
        operation: d.operation || d.callee
      };
      const l = d.line || d.location?.startLine;
      if (l) dbObj.line = l;
      return dbObj;
    });
  }

  return entry;
}

function writeCodeMap(codeMap, projectFolderName, config) {
  const targetDir = path.join(config.analysisOutputDir, projectFolderName);
  if (!fs.existsSync(targetDir)) {
    fs.mkdirSync(targetDir, { recursive: true });
  }

  const mapPath = path.join(targetDir, 'repository-code-map.json');
  fs.writeFileSync(mapPath, JSON.stringify(codeMap, null, 2), 'utf8');
  return mapPath;
}

module.exports = {
  buildCodeMap,
  writeCodeMap,
  toStreamlinedFileEntry
};
