const fs = require('fs');
const path = require('path');
const { classifyDirectory, classifyFileDetailed } = require('./filter');
const { registry } = require('./languageRegistry');
const { logger } = require('./logger');

function scanProject(projectPath, config, analysisId = null) {
  const t0 = Date.now();
  logger.info('PROJECT_SCAN', `Scanning project directory: ${projectPath}`, { analysisId });
  logger.stage('PROJECT_SCAN', 'START', { analysisId, path: projectPath });

  const files = [];
  const unsupportedFiles = [];
  let totalFilesDiscovered = 0;
  let totalDirectoriesDiscovered = 0;
  const directoriesSkippedByReason = {};
  const filesSkippedByReason = {};

  function walk(currentPath) {
    let entries;
    try {
      entries = fs.readdirSync(currentPath, { withFileTypes: true });
    } catch (err) {
      logger.warn('PROJECT_SCAN', `Unable to read directory: ${currentPath}`, { analysisId, error: err.message });
      return;
    }

    // Sort entries for deterministic scanning order
    entries.sort((a, b) => a.name.localeCompare(b.name));

    for (const entry of entries) {
      const fullPath = path.join(currentPath, entry.name);

      if (entry.isDirectory()) {
        totalDirectoriesDiscovered++;
        const relDirPath = path.relative(projectPath, fullPath).replace(/\\/g, '/') || entry.name;
        let dirClass = classifyDirectory(entry.name, config.ignoreDirs);
        if (!dirClass.shouldSkip) {
          if (fs.existsSync(path.join(fullPath, 'pyvenv.cfg'))) {
            dirClass = { shouldSkip: true, reason: 'virtual_environment' };
          }
        }

        if (dirClass.shouldSkip) {
          const reason = dirClass.reason || 'ignored_directory';
          directoriesSkippedByReason[reason] = (directoriesSkippedByReason[reason] || 0) + 1;
          logger.info('PROJECT_SCAN', `SKIP_DIRECTORY path=${relDirPath} reason=${reason}`, { analysisId });
          // DO NOT recurse into skipped directories (pruned before entering)
        } else {
          walk(fullPath);
        }
      } else if (entry.isFile()) {
        totalFilesDiscovered++;
        const relativePath = path.relative(projectPath, fullPath).replace(/\\/g, '/');
        const detailed = classifyFileDetailed(fullPath, relativePath, config.supportedExtensions);
        const ext = path.extname(entry.name).toLowerCase();

        if (detailed.classification === 'application_source') {
          const resolved = registry.resolveLanguage(ext);
          files.push({
            absolutePath: fullPath,
            relativePath,
            fileName: entry.name,
            extension: ext,
            language: resolved ? resolved.language : null
          });
          logger.info('FILE_FILTERING', `ACCEPT file=${relativePath} reason=${detailed.reason}`, { analysisId });
        } else {
          const reason = detailed.reason || 'unsupported_language';
          filesSkippedByReason[reason] = (filesSkippedByReason[reason] || 0) + 1;
          logger.info('FILE_FILTERING', `SKIP file=${relativePath} reason=${reason}`, { analysisId });

          if (detailed.classification === 'unsupported') {
            unsupportedFiles.push({
              filePath: fullPath,
              relativePath,
              extension: ext,
              reason: detailed.reason
            });
          }
        }
      }
    }
  }

  try {
    walk(projectPath);
    const duration_ms = Date.now() - t0;

    const stats = {
      totalFilesDiscovered,
      totalDirectoriesDiscovered,
      analyzableSourceFiles: files.length,
      supportedFiles: files.length,
      unsupportedFiles: unsupportedFiles.length,
      directoriesSkippedByReason,
      filesSkippedByReason,
      // Backward compatibility fields
      testFilesExcluded: filesSkippedByReason['test_file'] || 0,
      binaryFilesExcluded: filesSkippedByReason['binary_file'] || 0,
      dirsSkippedIgnored: Object.values(directoriesSkippedByReason).reduce((a, b) => a + b, 0),
      dirsSkippedTest: directoriesSkippedByReason['test_directory'] || 0
    };

    const summaryLines = [
      '==================================================',
      'PROJECT SCAN SUMMARY',
      '==================================================',
      `Project Root:               ${projectPath}`,
      `Scan Duration:              ${duration_ms}ms`,
      `Directories Discovered:     ${totalDirectoriesDiscovered}`,
      `Files Discovered:           ${totalFilesDiscovered}`,
      `Analyzable Source Files:    ${files.length}`,
      '--------------------------------------------------',
      'Directories Skipped By Reason:'
    ];
    for (const [r, count] of Object.entries(directoriesSkippedByReason)) {
      summaryLines.push(`  - ${r}: ${count}`);
    }
    if (Object.keys(directoriesSkippedByReason).length === 0) {
      summaryLines.push('  (none)');
    }
    summaryLines.push('Files Skipped By Reason:');
    for (const [r, count] of Object.entries(filesSkippedByReason)) {
      summaryLines.push(`  - ${r}: ${count}`);
    }
    if (Object.keys(filesSkippedByReason).length === 0) {
      summaryLines.push('  (none)');
    }
    summaryLines.push('==================================================');

    logger.info('PROJECT_SCAN', '\n' + summaryLines.join('\n'), { analysisId });

    logger.stage('PROJECT_SCAN', 'SUCCESS', {
      analysisId,
      duration_ms,
      totalFilesDiscovered,
      analyzableSourceFiles: files.length,
      directoriesSkipped: Object.values(directoriesSkippedByReason).reduce((a, b) => a + b, 0)
    });

    files.unsupportedFiles = unsupportedFiles;
    files.stats = stats;
    return files;
  } catch (err) {
    const duration_ms = Date.now() - t0;
    logger.stage('PROJECT_SCAN', 'FAILURE', { analysisId, duration_ms, error: err });
    throw err;
  }
}

module.exports = { scanProject };
