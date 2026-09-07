const fs = require('fs');
const path = require('path');
const { v4: uuidv4 } = require('uuid');
const { scanProject } = require('./scanner');
const { parseFile, initParser } = require('./parser');
const { extractInfo } = require('./extractor');
const { normalizeForPath } = require('./artifactWriter');
const { buildCodeMap, writeCodeMap } = require('./codeMapBuilder');
const { sendCodeMap } = require('./backendClient');
const { logger } = require('./logger');

async function runAnalysis(projectPath, statusTracker, config) {
  const analysisStart = Date.now();
  const analysisId = uuidv4();

  try {
    statusTracker.analyzing = true;
    statusTracker.error = null;
    statusTracker.stage = 'Initializing';
    statusTracker.progress = null;
    statusTracker.projectId = null;
    statusTracker.analysisId = analysisId;

    logger.info('PROJECT_SELECTION', `ANALYSIS STARTED for project: ${projectPath}`, { analysisId });
    logger.stage('PROJECT_SELECTION', 'SUCCESS', { analysisId, path: projectPath });

    // Pre-initialize parser
    await initParser();

    // 1. Scan Project (dynamically discovers files and verifies available grammars)
    statusTracker.stage = 'Scanning project files';
    logger.info('PROJECT_SCAN', 'PROJECT SCAN STARTED', { analysisId });
    const t0_scan = Date.now();
    const projectFolderName = path.basename(projectPath);

    // Reset previous parse directory to ensure no stale artifacts remain
    const projectDir = path.join(config.analysisOutputDir, projectFolderName);
    const parseDir = path.join(projectDir, normalizeForPath(projectFolderName) + '_parse');
    if (fs.existsSync(parseDir)) {
      try {
        fs.rmSync(parseDir, { recursive: true, force: true });
        logger.info('ANALYSIS_INIT', `Cleaned previous parse artifacts in: ${parseDir}`, { analysisId });
      } catch (rmErr) {
        logger.warn('ANALYSIS_INIT', `Could not clean parseDir: ${rmErr.message}`, { analysisId });
      }
    }

    const files = scanProject(projectPath, config, analysisId);
    const scanStats = files.stats || {};
    const unsupportedFiles = files.unsupportedFiles || [];
    scanStats.unsupportedFileList = unsupportedFiles;
    const dur_scan = Date.now() - t0_scan;
    logger.info('PROJECT_SCAN', `PROJECT SCAN COMPLETED in ${dur_scan}ms (${files.length} supported application files found, ${unsupportedFiles.length} unsupported files)`, { analysisId });

    if (files.length === 0) {
      const errDetail = unsupportedFiles.length > 0
        ? `No supported source files found. Found ${unsupportedFiles.length} unsupported file(s): ${unsupportedFiles.map(u => u.extension || u.relativePath).slice(0, 5).join(', ')}`
        : `No source files found in directory: ${projectPath}`;
      throw new Error(errDetail);
    }

    // 2. Parse source files & extract AST
    statusTracker.stage = 'Parsing source files';
    statusTracker.progress = { current: 0, total: files.length };
    logger.info('TREE_SITTER_PARSE', `TREE-SITTER PARSING STARTED for ${files.length} files`, { analysisId });
    const t0_parse = Date.now();

    const allArtifacts = [];

    for (let i = 0; i < files.length; i++) {
      const fileInfo = files[i];
      const t0_file = Date.now();
      logger.stage('TREE_SITTER_PARSE', 'START', { analysisId, file: fileInfo.relativePath, language: fileInfo.language });

      try {
        const parseResult = await parseFile(fileInfo);
        const dur_file_parse = Date.now() - t0_file;

        if (parseResult.hasErrors) {
          logger.warn('TREE_SITTER_PARSE', `Parse warnings in: ${fileInfo.relativePath}`, { analysisId, errors: parseResult.errors.join('; ') });
        }
        logger.stage('TREE_SITTER_PARSE', 'SUCCESS', { analysisId, file: fileInfo.relativePath, duration_ms: dur_file_parse });

        // AST Extraction
        logger.stage('AST_EXTRACTION', 'START', { analysisId, file: fileInfo.relativePath });
        const t0_extract = Date.now();
        const extractedInfo = extractInfo(fileInfo, parseResult);
        const dur_extract = Date.now() - t0_extract;
        logger.stage('AST_EXTRACTION', 'SUCCESS', { analysisId, file: fileInfo.relativePath, duration_ms: dur_extract });

        allArtifacts.push(extractedInfo);
      } catch (err) {
        const dur_fail = Date.now() - t0_file;
        logger.stage('TREE_SITTER_PARSE', 'FAILURE', {
          analysisId,
          file: fileInfo.relativePath,
          duration_ms: dur_fail,
          error_type: err.name || 'Error',
          error_message: err.message,
          error: err
        });

        allArtifacts.push({
          file: {
            path: fileInfo.absolutePath,
            relativePath: fileInfo.relativePath,
            name: fileInfo.fileName,
            extension: fileInfo.extension,
            language: fileInfo.language,
            lineCount: 0
          },
          parse: { status: 'error', errors: [err.message] },
          symbols: [],
          imports: [],
          exports: [],
          calls: [],
          references: [],
          classes: [],
          react: { components: [], jsxElements: [] },
          routes: [],
          databaseOperations: []
        });
      }

      statusTracker.progress.current = i + 1;
    }

    const dur_total_parse = Date.now() - t0_parse;
    logger.info('TREE_SITTER_PARSE', `TREE-SITTER PARSING COMPLETED in ${dur_total_parse}ms`, { analysisId });

    // 3. Build Code Map
    statusTracker.stage = 'Building repository Code Map';
    statusTracker.progress = null;
    logger.stage('CODE_MAP_CREATION', 'START', { analysisId });
    const t0_cmap = Date.now();

    const projectInfo = { name: projectFolderName, path: projectPath, analysis_id: analysisId };
    const codeMap = buildCodeMap(projectInfo, allArtifacts, scanStats);
    codeMap.analysis_id = analysisId;

    const codeMapPath = writeCodeMap(codeMap, projectFolderName, config);
    const dur_cmap = Date.now() - t0_cmap;
    logger.info('CODE_MAP_CREATION', `CODE MAP CREATED at ${codeMapPath} in ${dur_cmap}ms`, { analysisId });
    logger.stage('CODE_MAP_CREATION', 'SUCCESS', { analysisId, duration_ms: dur_cmap, path: codeMapPath });

    // 4. Send to backend
    statusTracker.stage = 'Sending to backend';
    const { projectId } = await sendCodeMap(codeMap, config);

    // 5. Complete
    const totalDuration = Date.now() - analysisStart;
    statusTracker.stage = 'Complete';
    statusTracker.projectId = projectId;
    statusTracker.progress = { current: files.length, total: files.length };

    logger.info('ANALYSIS_COMPLETE', `ANALYSIS COMPLETED in ${totalDuration}ms. Project ID: ${projectId}. Stats: ${JSON.stringify(codeMap.stats)}`, { analysisId });
    logger.stage('ANALYSIS_COMPLETE', 'SUCCESS', { analysisId, duration_ms: totalDuration, projectId });

  } catch (err) {
    const totalDuration = Date.now() - analysisStart;
    logger.stage('ANALYSIS_COMPLETE', 'FAILURE', {
      analysisId,
      duration_ms: totalDuration,
      error_type: err.name || 'Error',
      error_message: err.message,
      error: err
    });
    statusTracker.stage = 'Error';
    statusTracker.error = err.message;
  } finally {
    statusTracker.analyzing = false;
  }
}

module.exports = { runAnalysis };
