const fs = require('fs');
const path = require('path');
const config = require('./config');
const { logger } = require('./logger');

/**
 * Normalizes a file path to use forward slashes and removes leading slashes.
 */
function normalizePath(p) {
  if (!p) return '';
  return p.replace(/\\/g, '/').replace(/^\.\//, '').replace(/^\/+/, '');
}

/**
 * Resolves the project artifact directory containing repository-code-map.json,
 * test-cases/, feature-context/, and code retrieved/.
 */
function resolveProjectArtifactDir(options = {}) {
  const { codeMapPath, projectPath, projectId } = options;

  // 1. Direct codeMapPath provided
  if (codeMapPath && fs.existsSync(codeMapPath)) {
    const stats = fs.statSync(codeMapPath);
    return stats.isDirectory() ? codeMapPath : path.dirname(codeMapPath);
  }

  const baseOutputDir = config.analysisOutputDir;
  if (!fs.existsSync(baseOutputDir)) {
    return null;
  }

  // 2. Direct projectPath provided
  if (projectPath) {
    const folderName = path.basename(projectPath);
    const candidate = path.join(baseOutputDir, folderName);
    if (fs.existsSync(candidate) && fs.existsSync(path.join(candidate, 'repository-code-map.json'))) {
      return candidate;
    }

    // Search subdirectories for matching project.path in repository-code-map.json
    try {
      const entries = fs.readdirSync(baseOutputDir, { withFileTypes: true });
      for (const entry of entries) {
        if (entry.isDirectory()) {
          const subDir = path.join(baseOutputDir, entry.name);
          const mapFile = path.join(subDir, 'repository-code-map.json');
          if (fs.existsSync(mapFile)) {
            try {
              const codeMap = JSON.parse(fs.readFileSync(mapFile, 'utf8'));
              if (
                codeMap.project &&
                (codeMap.project.path === projectPath ||
                  path.resolve(codeMap.project.path) === path.resolve(projectPath) ||
                  codeMap.project.name === folderName)
              ) {
                return subDir;
              }
            } catch {}
          }
        }
      }
    } catch (err) {
      logger.warn('RETRIEVAL_ENGINE', `Error scanning analysisOutputDir: ${err.message}`);
    }
  }

  // 3. projectId provided
  if (projectId) {
    try {
      const entries = fs.readdirSync(baseOutputDir, { withFileTypes: true });
      for (const entry of entries) {
        if (entry.isDirectory()) {
          const subDir = path.join(baseOutputDir, entry.name);
          const mapFile = path.join(subDir, 'repository-code-map.json');
          if (fs.existsSync(mapFile)) {
            try {
              const codeMap = JSON.parse(fs.readFileSync(mapFile, 'utf8'));
              if (
                codeMap.analysis_id === projectId ||
                codeMap.project?.projectId === projectId ||
                codeMap.project?.id === projectId
              ) {
                return subDir;
              }
            } catch {}
          }
        }
      }
    } catch {}
  }

  return null;
}

/**
 * Extracts balanced code block for a symbol starting at startLine (1-indexed).
 * If language is Python or brace-less, uses indentation; otherwise uses brace counting.
 */
function extractSymbolCodeBlock(lines, startLine, language = '') {
  if (!lines || lines.length === 0) return '';
  const idx = Math.max(0, startLine - 1);
  if (idx >= lines.length) return lines.join('\n');

  const lang = (language || '').toLowerCase();
  const isIndentationBased = ['python', 'py'].includes(lang);

  if (isIndentationBased) {
    // Determine base indentation of start line
    const declLine = lines[idx];
    const match = declLine.match(/^(\s*)/);
    const baseIndent = match ? match[1].length : 0;

    let endIdx = idx + 1;
    while (endIdx < lines.length) {
      const line = lines[endIdx];
      // Empty lines or comments inside function are fine
      if (line.trim() === '' || line.trim().startsWith('#')) {
        endIdx++;
        continue;
      }
      const lineIndent = (line.match(/^(\s*)/) || [''])[0].length;
      if (lineIndent <= baseIndent) {
        break;
      }
      endIdx++;
    }
    return lines.slice(idx, endIdx).join('\n');
  }

  // Brace-based extraction
  let openBraces = 0;
  let hasBrace = false;
  let endIdx = idx;

  for (let i = idx; i < lines.length; i++) {
    const line = lines[i];
    const opens = (line.match(/\{/g) || []).length;
    const closes = (line.match(/\}/g) || []).length;

    if (opens > 0) hasBrace = true;
    openBraces += (opens - closes);

    endIdx = i + 1;
    if (hasBrace && openBraces <= 0) {
      break;
    }
  }

  // If no braces were encountered (e.g. single-expression arrow function), extract until semicolon or next empty line
  if (!hasBrace) {
    endIdx = Math.min(lines.length, idx + 10);
    for (let i = idx; i < endIdx; i++) {
      if (lines[i].includes(';') || lines[i].trim() === '') {
        endIdx = i + 1;
        break;
      }
    }
  }

  return lines.slice(idx, endIdx).join('\n');
}

/**
 * Extracts minimal sufficient source context:
 * - If file is <= 150 lines, return full source to keep complete imports and closures.
 * - If larger, extract import headers at top + declaration block.
 */
function extractSufficientSource(rawContent, targetSymbol, symbolDecl, language) {
  if (!rawContent) return { sourceCode: '', sourceRanges: [] };
  const lines = rawContent.split(/\r?\n/);
  if (lines.length <= 150) {
    return {
      sourceCode: rawContent,
      sourceRanges: [{ startLine: 1, endLine: lines.length }]
    };
  }

  const startLine = Math.max(1, symbolDecl?.location?.startLine || symbolDecl?.line || symbolDecl?.startLine || 1);
  const symbolBlock = extractSymbolCodeBlock(lines, startLine, language);
  const blockLinesCount = symbolBlock.split(/\r?\n/).length;
  const endLine = startLine + blockLinesCount - 1;

  // Extract top-level imports/requires (first 40 lines max)
  const importLines = [];
  const maxImportSearch = Math.min(40, startLine - 1);
  for (let i = 0; i < maxImportSearch; i++) {
    const line = lines[i];
    const trimmed = line.trim();
    if (
      trimmed.startsWith('import ') ||
      (trimmed.startsWith('const ') && trimmed.includes('require(')) ||
      (trimmed.startsWith('var ') && trimmed.includes('require(')) ||
      (trimmed.startsWith('let ') && trimmed.includes('require(')) ||
      trimmed.startsWith('#include') ||
      trimmed.startsWith('from ') ||
      trimmed.startsWith('package ')
    ) {
      importLines.push(line);
    }
  }

  let combined = '';
  if (importLines.length > 0) {
    combined += importLines.join('\n') + '\n\n// ... [Target declaration extracted below] ...\n\n';
  }
  combined += symbolBlock;

  return {
    sourceCode: combined,
    sourceRanges: [
      { startLine: 1, endLine: Math.min(importLines.length, 40), reason: 'imports' },
      { startLine, endLine, reason: 'target_symbol' }
    ]
  };
}

/**
 * Main retrieval engine logic.
 * Reads test cases, resolves targets using repository-code-map.json,
 * reads original source code from disk, and saves to `code retrieved/<same-filename>`.
 */
async function retrieveCodeForProject(options = {}) {
  const t0 = Date.now();
  const artifactDir = resolveProjectArtifactDir(options);

  if (!artifactDir) {
    const msg = 'Could not locate project artifact directory with repository-code-map.json';
    logger.warn('RETRIEVAL_ENGINE', msg, options);
    return {
      status: 'error',
      message: msg,
      retrievedCount: 0,
      savedFiles: []
    };
  }

  const codeMapFile = path.join(artifactDir, 'repository-code-map.json');
  if (!fs.existsSync(codeMapFile)) {
    return {
      status: 'error',
      message: `repository-code-map.json not found in ${artifactDir}`,
      retrievedCount: 0,
      savedFiles: []
    };
  }

  let codeMap;
  try {
    codeMap = JSON.parse(fs.readFileSync(codeMapFile, 'utf8'));
  } catch (err) {
    return {
      status: 'error',
      message: `Failed to parse repository-code-map.json: ${err.message}`,
      retrievedCount: 0,
      savedFiles: []
    };
  }

  const originalProjectRoot = codeMap.project?.path;
  if (!originalProjectRoot || !fs.existsSync(originalProjectRoot)) {
    logger.warn('RETRIEVAL_ENGINE', `Original project path does not exist on disk: ${originalProjectRoot}`);
  }

  const testCasesDir = path.join(artifactDir, 'test-cases');
  if (!fs.existsSync(testCasesDir)) {
    return {
      status: 'not_found',
      message: `No test-cases directory found in ${artifactDir}`,
      retrievedCount: 0,
      savedFiles: []
    };
  }

  // Ensure `code retrieved` output directory exists dynamically
  const codeRetrievedDir = path.join(artifactDir, 'code retrieved');
  if (!fs.existsSync(codeRetrievedDir)) {
    fs.mkdirSync(codeRetrievedDir, { recursive: true });
    logger.info('RETRIEVAL_ENGINE', `Created directory: ${codeRetrievedDir}`);
  }

  // In-memory index of codeMap files
  const filesIndex = new Map();
  const rawFiles = codeMap.files || {};
  const addToFileIndex = (item, rawPath) => {
    if (!rawPath) return;
    const norm = normalizePath(rawPath);
    filesIndex.set(norm, item);
    filesIndex.set(norm.toLowerCase(), item);
    filesIndex.set(rawPath.toLowerCase(), item);
    const base = path.basename(rawPath);
    if (!filesIndex.has(base)) filesIndex.set(base, item);
    if (!filesIndex.has(base.toLowerCase())) filesIndex.set(base.toLowerCase(), item);
  };

  if (Array.isArray(rawFiles)) {
    for (const fileData of rawFiles) {
      const relPath = fileData.relativePath || fileData.file?.relativePath || fileData.path || fileData.file?.path || '';
      const item = { relPath, ...fileData };
      addToFileIndex(item, relPath);
      if (fileData.file?.path) addToFileIndex(item, fileData.file.path);
      if (fileData.file?.relativePath) addToFileIndex(item, fileData.file.relativePath);
      if (fileData.file?.name) addToFileIndex(item, fileData.file.name);
    }
  } else {
    for (const [relPath, fileData] of Object.entries(rawFiles)) {
      const actualPath = fileData?.relativePath || fileData?.file?.relativePath || relPath;
      const item = { relPath: actualPath, ...fileData };
      addToFileIndex(item, actualPath);
      if (fileData?.file?.relativePath) addToFileIndex(item, fileData.file.relativePath);
      if (fileData?.file?.name) addToFileIndex(item, fileData.file.name);
    }
  }

  // In-memory index of relationships
  const relationships = codeMap.relationships || [];
  const relByFrom = new Map();
  for (const r of relationships) {
    const fromNorm = normalizePath(r.from);
    if (!relByFrom.has(fromNorm)) relByFrom.set(fromNorm, []);
    relByFrom.get(fromNorm).push(r);
  }

  // Read all test-case JSON files
  const testCaseFiles = fs
    .readdirSync(testCasesDir)
    .filter((f) => f.endsWith('.json'));

  if (testCaseFiles.length === 0) {
    return {
      status: 'not_found',
      message: 'No test case files found in test-cases directory.',
      retrievedCount: 0,
      savedFiles: []
    };
  }

  const savedFiles = [];
  const allRetrievedCases = [];
  const diskFileCache = new Map(); // path -> fileContent

  for (const tcFileName of testCaseFiles) {
    const tcFilePath = path.join(testCasesDir, tcFileName);
    let rawContent;
    try {
      rawContent = JSON.parse(fs.readFileSync(tcFilePath, 'utf8'));
    } catch (err) {
      logger.warn('RETRIEVAL_ENGINE', `Could not parse test case file ${tcFileName}: ${err.message}`);
      continue;
    }

    // Support both `{ group: "...", testCases: [...] }` and single test case object
    const testCasesList = Array.isArray(rawContent.testCases)
      ? rawContent.testCases
      : Array.isArray(rawContent)
      ? rawContent
      : [rawContent];

    const processedTestCases = [];

    for (const tc of testCasesList) {
      const testCaseId = tc.testCaseId || 'TC-UNKNOWN';
      const target = tc.unit || tc.target || {};
      const targetFileRaw = target.file || '';
      const targetSymbol = target.symbol || '';
      const targetType = target.type || 'function';

      const warnings = [];
      let retrievalStatus = 'resolved';

      // 1. Resolve target file in Code Map
      const normTargetFile = normalizePath(targetFileRaw);
      const baseTargetFile = path.basename(targetFileRaw);
      const codeMapEntry =
        filesIndex.get(normTargetFile) ||
        filesIndex.get(normTargetFile.toLowerCase()) ||
        filesIndex.get(targetFileRaw.toLowerCase()) ||
        filesIndex.get(baseTargetFile) ||
        filesIndex.get(baseTargetFile.toLowerCase());

      if (!codeMapEntry) {
        retrievalStatus = 'target_not_found';
        warnings.push(`Target file '${targetFileRaw}' not found in repository Code Map.`);
      }

      // 2. Resolve target symbol in declarations, symbols, and classes
      let symbolDecl = null;
      if (codeMapEntry && targetSymbol) {
        const decls = [
          ...(codeMapEntry.declarations || []),
          ...(codeMapEntry.symbols || []),
          ...(codeMapEntry.classes || [])
        ];
        symbolDecl = decls.find(
          (d) => d.name === targetSymbol || (d.name && d.name.toLowerCase() === targetSymbol.toLowerCase())
        );

        if (!symbolDecl) {
          // Check methods/symbols inside parent classes or objects
          for (const d of decls) {
            const nested = [
              ...(Array.isArray(d.methods) ? d.methods : []),
              ...(Array.isArray(d.symbols) ? d.symbols : []),
              ...(Array.isArray(d.functions) ? d.functions : [])
            ];
            const match = nested.find(
              (m) => m.name === targetSymbol || (m.name && m.name.toLowerCase() === targetSymbol.toLowerCase())
            );
            if (match) {
              symbolDecl = { ...match, parentClass: d.name };
              break;
            }
          }
        }

        if (!symbolDecl && target.route && Array.isArray(codeMapEntry.routes)) {
          const rMatch = codeMapEntry.routes.find(
            (r) => (r.path === target.route || r.route === target.route)
          );
          if (rMatch) {
            symbolDecl = { name: rMatch.handler || targetSymbol || rMatch.path, line: rMatch.line, type: 'route' };
          }
        }

        if (!symbolDecl) {
          retrievalStatus = 'partially_resolved';
          warnings.push(
            `Target file found, but target symbol '${targetSymbol || target.route}' was not found in Code Map declarations.`
          );
        }
      }

      // 3. Retrieve actual source code from original project on disk
      const sourceFiles = [];
      let sourceRanges = [];
      const effectiveRelPath = codeMapEntry?.relPath || codeMapEntry?.file?.relativePath || codeMapEntry?.file?.path || targetFileRaw;
      const detectedLang = codeMapEntry?.language || codeMapEntry?.file?.language || codeMap.project?.languagesDetected?.[0] || '';

      if (codeMapEntry && originalProjectRoot) {
        const diskPath = path.isAbsolute(effectiveRelPath)
          ? effectiveRelPath
          : path.resolve(originalProjectRoot, effectiveRelPath);

        if (fs.existsSync(diskPath)) {
          let fileContent = diskFileCache.get(diskPath);
          if (!fileContent) {
            try {
              fileContent = fs.readFileSync(diskPath, 'utf8');
              diskFileCache.set(diskPath, fileContent);
            } catch (err) {
              retrievalStatus = 'source_not_found';
              warnings.push(`Could not read source file at ${diskPath}: ${err.message}`);
            }
          }

          if (fileContent) {
            const extracted = extractSufficientSource(
              fileContent,
              targetSymbol,
              symbolDecl,
              detectedLang
            );

            sourceFiles.push({
              path: normalizePath(effectiveRelPath),
              reason: 'primary_target',
              symbol: targetSymbol || (symbolDecl ? symbolDecl.name : null),
              sourceCode: extracted.sourceCode
            });
            sourceRanges = extracted.sourceRanges || [];
          }
        } else {
          retrievalStatus = 'source_not_found';
          warnings.push(`Source file does not exist on disk: ${diskPath}`);
        }
      } else if (!originalProjectRoot) {
        retrievalStatus = 'source_not_found';
        warnings.push('Original project path not specified in repository Code Map.');
      }

      // 4. Dependency Traversal (Default depth = 0; depth = 1 for internal dependencies)
      const relatedFiles = [];
      const relatedSymbols = [];
      let dependencyDepth = 0;

      if (codeMapEntry) {
        const fileRelationships = relByFrom.get(normTargetFile) || [];
        for (const rel of fileRelationships) {
          if (!rel.external && rel.to) {
            const relToNorm = normalizePath(rel.to);
            if (!relatedFiles.includes(relToNorm)) {
              relatedFiles.push(relToNorm);
            }
            if (rel.evidence?.symbol && !relatedSymbols.includes(rel.evidence.symbol)) {
              relatedSymbols.push(rel.evidence.symbol);
            }
          }
        }
      }

      // Construct per-test-case retrieved artifact
      const tcResult = {
        testCaseId,
        title: tc.title || '',
        description: tc.description || '',
        testType: tc.testType || 'unit',
        priority: tc.priority || 'medium',
        target: {
          file: targetFileRaw,
          symbol: targetSymbol,
          type: targetType,
          route: target.route || null,
          httpMethod: target.httpMethod || null
        },
        retrieval: {
          status: retrievalStatus,
          primaryFile: effectiveRelPath,
          primarySymbol: targetSymbol || (symbolDecl ? symbolDecl.name : null),
          relatedFiles,
          relatedSymbols,
          sourceRanges,
          dependencyDepth
        },
        source: {
          files: sourceFiles
        },
        metadata: {
          relatedFiles,
          relatedSymbols,
          dependencyDepth,
          language: detectedLang || null,
          lineCount: codeMapEntry?.lineCount || codeMapEntry?.file?.lineCount || 0
        },
        warnings
      };

      processedTestCases.push(tcResult);
      allRetrievedCases.push(tcResult);
    }

    // Construct persistent file structure
    // Matches the test case file name exactly: `code retrieved/<tcFileName>`
    const firstCase = processedTestCases[0] || {};
    const retrievedFileContent = {
      testCaseId: firstCase.testCaseId || 'TC-001',
      group: rawContent.group || rawContent.title || 'Unit Tests',
      testType: firstCase.testType || 'unit',
      source: firstCase.source || { files: [] },
      metadata: firstCase.metadata || { relatedFiles: [], relatedSymbols: [], dependencyDepth: 0 },
      retrieval: firstCase.retrieval || { status: 'resolved' },
      testCases: processedTestCases
    };

    const outPath = path.join(codeRetrievedDir, tcFileName);
    fs.writeFileSync(outPath, JSON.stringify(retrievedFileContent, null, 2), 'utf8');
    savedFiles.push(tcFileName);
    logger.info('RETRIEVAL_ENGINE', `Saved retrieved code artifact to ${outPath}`);
  }

  const durationMs = Date.now() - t0;
  logger.info(
    'RETRIEVAL_ENGINE',
    `Completed code retrieval in ${durationMs}ms: ${allRetrievedCases.length} test cases processed, ${savedFiles.length} files saved.`
  );

  return {
    status: 'success',
    retrievedCount: allRetrievedCases.length,
    savedFiles,
    codeRetrievedDir,
    durationMs,
    message: `Retrieved source code for ${allRetrievedCases.length} test case(s) across ${savedFiles.length} file(s).`
  };
}

/**
 * Discovers and reads retrieved-code artifacts from `code retrieved/`.
 */
function getRetrievedCodeFiles(options = {}) {
  const artifactDir = resolveProjectArtifactDir(options);
  if (!artifactDir) {
    return { status: 'not_found', files: [], message: 'Artifact directory not found' };
  }

  const codeRetrievedDir = path.join(artifactDir, 'code retrieved');
  if (!fs.existsSync(codeRetrievedDir)) {
    return { status: 'not_found', files: [], message: 'code retrieved directory does not exist' };
  }

  const files = fs
    .readdirSync(codeRetrievedDir)
    .filter((f) => f.endsWith('.json'));

  const results = [];
  for (const fileName of files) {
    const filePath = path.join(codeRetrievedDir, fileName);
    try {
      const content = JSON.parse(fs.readFileSync(filePath, 'utf8'));
      results.push({
        fileName,
        filePath,
        content
      });
    } catch (err) {
      logger.warn('RETRIEVAL_ENGINE', `Could not read retrieved file ${fileName}: ${err.message}`);
    }
  }

  return {
    status: 'success',
    count: results.length,
    files: results,
    codeRetrievedDir
  };
}

module.exports = {
  retrieveCodeForProject,
  getRetrievedCodeFiles,
  resolveProjectArtifactDir,
  extractSufficientSource
};
