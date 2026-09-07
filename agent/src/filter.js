const path = require('path');
const { registry } = require('./languageRegistry');

const VIRTUAL_ENV_DIRS = new Set([
  '.venv',
  'venv',
  'env',
  'ENV',
  '.env',
  '.virtualenv',
  'virtualenv'
]);

const VERSION_CONTROL_DIRS = new Set([
  '.git',
  '.svn',
  '.hg',
  '.bzr'
]);

const DEPENDENCY_DIRS = new Set([
  'node_modules',
  'vendor',
  'bower_components',
  'packages'
]);

const CACHE_DIRS = new Set([
  '__pycache__',
  '.pytest_cache',
  '.mypy_cache',
  '.ruff_cache',
  '.cache',
  '.parcel-cache',
  '.turbo',
  '.eslintcache'
]);

const BUILD_OUTPUT_DIRS = new Set([
  'dist',
  'build',
  'out',
  'target',
  'bin',
  'obj',
  '.next',
  '.nuxt'
]);

const COVERAGE_DIRS = new Set([
  'coverage',
  '.nyc_output'
]);

const TEST_DIR_NAMES = new Set([
  'test',
  'tests',
  '__tests__',
  '__test__',
  'spec',
  'specs',
  'e2e',
  'cypress',
  'playwright',
  'fixtures',
  'test-results'
]);

/**
 * Classifies a directory to determine if it should be pruned before entering.
 * Returns { shouldSkip: boolean, reason: string | null }
 */
function classifyDirectory(dirName, customIgnoreDirs = []) {
  if (!dirName) return { shouldSkip: false, reason: null };
  const lower = dirName.toLowerCase().trim();

  // 1. Virtual environments (.venv, venv, env, ENV)
  if (VIRTUAL_ENV_DIRS.has(dirName) || VIRTUAL_ENV_DIRS.has(lower)) {
    return { shouldSkip: true, reason: 'virtual_environment' };
  }

  // 2. Version control (.git, .svn, etc.)
  if (VERSION_CONTROL_DIRS.has(lower)) {
    return { shouldSkip: true, reason: 'version_control' };
  }

  // 3. Dependencies (node_modules, vendor, etc.)
  if (DEPENDENCY_DIRS.has(lower)) {
    return { shouldSkip: true, reason: 'dependency_directory' };
  }

  // 4. Cache directories (__pycache__, .pytest_cache, etc.)
  if (CACHE_DIRS.has(lower)) {
    return { shouldSkip: true, reason: 'cache_directory' };
  }

  // 5. Build outputs & compiler targets (dist, build, out, bin, obj, target, etc.)
  if (BUILD_OUTPUT_DIRS.has(lower)) {
    return { shouldSkip: true, reason: 'build_output' };
  }

  // 6. Coverage reports
  if (COVERAGE_DIRS.has(lower)) {
    return { shouldSkip: true, reason: 'coverage_directory' };
  }

  // 7. Test directories (test, tests, __tests__, spec, etc.)
  if (TEST_DIR_NAMES.has(lower)) {
    return { shouldSkip: true, reason: 'test_directory' };
  }

  // 8. Custom user-configured ignore directories
  if (customIgnoreDirs && Array.isArray(customIgnoreDirs)) {
    if (customIgnoreDirs.includes(dirName) || customIgnoreDirs.includes(lower)) {
      return { shouldSkip: true, reason: 'ignored_directory' };
    }
  }

  return { shouldSkip: false, reason: null };
}

function shouldIgnoreDir(dirName, ignoreDirs = []) {
  const result = classifyDirectory(dirName, ignoreDirs);
  return result.shouldSkip;
}

function isTestDir(dirName) {
  if (!dirName) return false;
  return TEST_DIR_NAMES.has(dirName.toLowerCase().trim());
}

/**
 * Checks if a relative path contains any directory segment classified as a test directory.
 */
function isTestPath(relativePath) {
  if (!relativePath) return false;
  const normalized = relativePath.replace(/\\/g, '/').toLowerCase();
  const segments = normalized.split('/');
  for (let i = 0; i < segments.length - 1; i++) {
    if (isTestDir(segments[i])) {
      return true;
    }
  }
  return false;
}

/**
 * Deterministic test file classification using path/file boundaries.
 * Matches:
 * - Files located within test directories (tests/, test/, __tests__/, etc.)
 * - *.test.*, *.spec.* (e.g. auth.test.js, app.spec.ts, .test.py)
 * - test_*, *_test, spec_*, *_spec (e.g. test_routes.py, user_test.go)
 * - test.*, tests.*, spec.*, specs.* (e.g. test.py, tests.js)
 * - Test runner configs (playwright.config.*, jest.config.*, conftest.py, etc.)
 * Does NOT exclude files merely containing 'test' as a substring:
 * e.g. testingService.py, contest.js, latestTransactions.js are NOT test files.
 */
function isTestFile(filePath, relativePath = '') {
  const targetPath = relativePath || filePath;
  if (!targetPath) return false;

  // 1. Path segment check: if any ancestor directory is a test directory
  if (isTestPath(targetPath)) {
    return true;
  }

  const baseName = path.basename(targetPath).toLowerCase();

  // 2. Pattern: *.test.*, *.spec.*, or starts with .test. or .spec.
  if (/\.(test|spec)\.[a-z0-9]+$/i.test(baseName) || /^\.(test|spec)\./i.test(baseName)) {
    return true;
  }

  // 3. Prefix/Suffix boundaries: test_*, *_test, spec_*, *_spec
  const nameWithoutExt = baseName.replace(/\.[a-z0-9]+$/i, '');
  if (/^test_/i.test(nameWithoutExt) || /_test$/i.test(nameWithoutExt)) {
    return true;
  }
  if (/^spec_/i.test(nameWithoutExt) || /_spec$/i.test(nameWithoutExt)) {
    return true;
  }
  if (nameWithoutExt === 'test' || nameWithoutExt === 'tests' || nameWithoutExt === 'spec' || nameWithoutExt === 'specs') {
    return true;
  }

  // 4. Test configuration and reporter files
  if (
    /(playwright|jest|vitest|cypress|karma)\.config\./i.test(baseName) ||
    baseName.includes('console-reporter') ||
    baseName.includes('test-reporter') ||
    baseName === 'conftest.py'
  ) {
    return true;
  }

  return false;
}

/**
 * Checks if a file is an environment file (.env, .env.*, etc.)
 */
function isEnvironmentFile(fileName) {
  if (!fileName) return false;
  const lower = fileName.toLowerCase().trim();
  return lower === '.env' || lower.startsWith('.env.') || lower.endsWith('.env') || lower === '.envrc';
}

const CONFIG_FILES = new Set([
  '.gitignore', '.gitattributes', '.gitmodules', '.gitkeep',
  '.editorconfig', '.dockerignore', 'dockerfile',
  'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'pipfile', 'pipfile.lock', 'poetry.lock',
  'license', 'licence', 'readme', 'readme.md', 'readme.txt',
  '.ds_store', 'thumbs.db'
]);

/**
 * Checks if a file is a tool/linter/package-manager configuration file.
 */
function isConfigFile(fileName) {
  if (!fileName) return false;
  const lower = fileName.toLowerCase().trim();
  if (CONFIG_FILES.has(lower)) return true;
  if (lower.startsWith('.prettier') || lower.startsWith('.eslint') || lower.startsWith('.babel') || lower.startsWith('.pylint') || lower.startsWith('.flake8')) {
    return true;
  }
  if (lower.startsWith('docker-compose') && (lower.endsWith('.yml') || lower.endsWith('.yaml'))) {
    return true;
  }
  if (lower.startsWith('requirements') && lower.endsWith('.txt')) {
    return true;
  }
  return false;
}

/**
 * Checks if a file is supported by an available Tree-sitter grammar.
 */
function isSupportedFile(filePath, supportedExtensions = null) {
  const ext = path.extname(filePath).toLowerCase();
  if (supportedExtensions && Array.isArray(supportedExtensions)) {
    return supportedExtensions.includes(ext);
  }
  return registry.isSupported(ext);
}

const BINARY_EXTENSIONS = new Set([
  '.class', '.jar', '.war', '.ear',
  '.exe', '.dll', '.so', '.dylib', '.o', '.obj', '.bin',
  '.pyc', '.pyo', '.pyd',
  '.wasm',
  '.png', '.jpg', '.jpeg', '.gif', '.ico', '.svg', '.webp', '.bmp', '.tiff',
  '.woff', '.woff2', '.ttf', '.eot', '.otf',
  '.mp4', '.webm', '.mp3', '.wav', '.ogg',
  '.zip', '.tar', '.gz', '.7z', '.rar',
  '.db', '.sqlite', '.sqlite3',
  '.pdf', '.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx'
]);

function isBinaryFile(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  return BINARY_EXTENSIONS.has(ext);
}

/**
 * Categorizes a file into exact classifications and descriptive reasons.
 */
function classifyFileDetailed(filePath, relativePath, supportedExtensions = null) {
  const fileName = path.basename(filePath);
  const ext = path.extname(filePath).toLowerCase();

  // 1. Environment files (.env, .env.local, etc.)
  if (isEnvironmentFile(fileName)) {
    return { classification: 'environment', reason: 'environment_file' };
  }

  // 2. Binary files
  if (isBinaryFile(filePath)) {
    return { classification: 'binary', reason: 'binary_file' };
  }

  // 3. Test files
  if (isTestFile(filePath, relativePath)) {
    return { classification: 'test', reason: 'test_file' };
  }

  // 4. Configuration and meta files
  if (isConfigFile(fileName)) {
    return { classification: 'configuration', reason: 'configuration_file' };
  }

  // 5. Supported application source files
  if (isSupportedFile(filePath, supportedExtensions)) {
    return { classification: 'application_source', reason: 'supported_source' };
  }

  // 6. Unsupported files
  return {
    classification: 'unsupported',
    reason: 'unsupported_language'
  };
}

function classifyFile(filePath, relativePath, supportedExtensions = null) {
  return classifyFileDetailed(filePath, relativePath, supportedExtensions).classification;
}

module.exports = {
  classifyDirectory,
  shouldIgnoreDir,
  isTestDir,
  isTestPath,
  isTestFile,
  isEnvironmentFile,
  isConfigFile,
  isSupportedFile,
  isBinaryFile,
  classifyFile,
  classifyFileDetailed
};
