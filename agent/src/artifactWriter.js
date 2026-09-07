const fs = require('fs');
const path = require('path');

function normalizeForPath(name) {
  return name.replace(/[^a-zA-Z0-9_-]/g, '_');
}

/**
 * Deterministic JSON artifact naming based on actual filename:
 * - app.py → app.json
 * - Card.java → Card.json
 * - logic.js → logic.json
 * - main.cpp → main.json
 * - server.ts → server.json
 *
 * If two files in different directories share the same base filename,
 * disambiguates deterministically using the repository-relative path.
 */
function getDeterministicArtifactName(relativePath, parseDir) {
  const parsed = path.parse(relativePath);
  const baseName = parsed.name; // filename without extension (e.g. 'Card', 'app')
  const defaultArtifactName = `${baseName}.json`;
  const defaultPath = path.join(parseDir, defaultArtifactName);

  // If no collision exists, use the clean filename (e.g. Card.json)
  if (!fs.existsSync(defaultPath)) {
    return defaultArtifactName;
  }

  // Check if existing file belongs to the same relative path
  try {
    const existingContent = JSON.parse(fs.readFileSync(defaultPath, 'utf8'));
    if (existingContent && existingContent.file && existingContent.file.relativePath === relativePath) {
      return defaultArtifactName;
    }
  } catch {
    // If unreadable, proceed with disambiguation
  }

  // Collision detected (different file with identical base name)
  // Disambiguate deterministically using the repository-relative directory + base name
  const relDir = parsed.dir.replace(/\\/g, '/');
  const safeDirPrefix = normalizeForPath(relDir).replace(/_+$/, '');
  return `${safeDirPrefix}_${baseName}.json`;
}

const { toStreamlinedFileEntry } = require('./codeMapBuilder');

function writeArtifact(extractedInfo, projectFolderName, config) {
  const projectDir = path.join(config.analysisOutputDir, projectFolderName);
  const parseDir = path.join(projectDir, normalizeForPath(projectFolderName) + '_parse');

  if (!fs.existsSync(parseDir)) {
    fs.mkdirSync(parseDir, { recursive: true });
  }

  const relPath = extractedInfo.file.relativePath;
  const filename = getDeterministicArtifactName(relPath, parseDir);
  const artifactPath = path.join(parseDir, filename);

  const streamlined = toStreamlinedFileEntry(extractedInfo);
  fs.writeFileSync(artifactPath, JSON.stringify(streamlined, null, 2), 'utf8');

  return artifactPath;
}

module.exports = {
  writeArtifact,
  normalizeForPath,
  getDeterministicArtifactName
};
