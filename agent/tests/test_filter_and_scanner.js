const assert = require('assert');
const fs = require('fs');
const path = require('path');
const os = require('os');
const {
  classifyDirectory,
  isTestFile,
  isEnvironmentFile,
  isConfigFile,
  classifyFileDetailed
} = require('../src/filter');
const { scanProject } = require('../src/scanner');
const { registry } = require('../src/languageRegistry');

console.log('=== RUNNING SCANNER AND FILTER UNIT TESTS ===');

// 1. Directory classification tests
console.log('\n[Test 1] Directory pruning classification:');
const dirTests = [
  { dir: '.venv', expectedSkip: true, expectedReason: 'virtual_environment' },
  { dir: 'venv', expectedSkip: true, expectedReason: 'virtual_environment' },
  { dir: 'env', expectedSkip: true, expectedReason: 'virtual_environment' },
  { dir: 'ENV', expectedSkip: true, expectedReason: 'virtual_environment' },
  { dir: '.virtualenv', expectedSkip: true, expectedReason: 'virtual_environment' },
  { dir: '.git', expectedSkip: true, expectedReason: 'version_control' },
  { dir: 'node_modules', expectedSkip: true, expectedReason: 'dependency_directory' },
  { dir: 'vendor', expectedSkip: true, expectedReason: 'dependency_directory' },
  { dir: '__pycache__', expectedSkip: true, expectedReason: 'cache_directory' },
  { dir: '.pytest_cache', expectedSkip: true, expectedReason: 'cache_directory' },
  { dir: '.mypy_cache', expectedSkip: true, expectedReason: 'cache_directory' },
  { dir: '.ruff_cache', expectedSkip: true, expectedReason: 'cache_directory' },
  { dir: '.cache', expectedSkip: true, expectedReason: 'cache_directory' },
  { dir: 'dist', expectedSkip: true, expectedReason: 'build_output' },
  { dir: 'build', expectedSkip: true, expectedReason: 'build_output' },
  { dir: 'target', expectedSkip: true, expectedReason: 'build_output' },
  { dir: 'bin', expectedSkip: true, expectedReason: 'build_output' },
  { dir: 'obj', expectedSkip: true, expectedReason: 'build_output' },
  { dir: 'out', expectedSkip: true, expectedReason: 'build_output' },
  { dir: 'coverage', expectedSkip: true, expectedReason: 'coverage_directory' },
  { dir: 'tests', expectedSkip: true, expectedReason: 'test_directory' },
  { dir: 'test', expectedSkip: true, expectedReason: 'test_directory' },
  { dir: '__tests__', expectedSkip: true, expectedReason: 'test_directory' },
  { dir: 'spec', expectedSkip: true, expectedReason: 'test_directory' },
  // Non-skipped application directories
  { dir: 'src', expectedSkip: false, expectedReason: null },
  { dir: 'app', expectedSkip: false, expectedReason: null },
  { dir: 'controllers', expectedSkip: false, expectedReason: null },
  { dir: 'services', expectedSkip: false, expectedReason: null },
  { dir: 'components', expectedSkip: false, expectedReason: null },
  { dir: 'utils', expectedSkip: false, expectedReason: null }
];

for (const { dir, expectedSkip, expectedReason } of dirTests) {
  const result = classifyDirectory(dir);
  assert.strictEqual(result.shouldSkip, expectedSkip, `Expected ${dir} skip=${expectedSkip}, got ${result.shouldSkip}`);
  assert.strictEqual(result.reason, expectedReason, `Expected ${dir} reason=${expectedReason}, got ${result.reason}`);
  console.log(`  ✓ Directory "${dir}": skip=${result.shouldSkip} reason=${result.reason || 'none'}`);
}

// 2. Semantic test file filtering tests
console.log('\n[Test 2] Semantic test file detection:');

const nonTestFiles = [
  'testingService.py',
  'contest.js',
  'latestTransactions.js',
  'testament.ts',
  'contestRules.java',
  'app.py',
  'auth.js',
  'calculator.ts',
  'contestant_profile.py'
];

for (const file of nonTestFiles) {
  const isTest = isTestFile(file);
  assert.strictEqual(isTest, false, `File "${file}" should NOT be classified as test file`);
  console.log(`  ✓ Preserved non-test file: "${file}"`);
}

const testFiles = [
  'auth.test.js',
  'user.spec.ts',
  '.test.py',
  'test_routes.py',
  'user_test.go',
  'test.py',
  'tests.js',
  'conftest.py',
  'playwright.config.js',
  'jest.config.ts',
  'tests/helpers.py'
];

for (const file of testFiles) {
  const isTest = isTestFile(file, file);
  assert.strictEqual(isTest, true, `File "${file}" MUST be classified as test file`);
  console.log(`  ✓ Detected test file: "${file}"`);
}

// 3. Environment file filtering tests
console.log('\n[Test 3] Environment file detection:');
const envFiles = ['.env', '.env.local', '.env.production', '.env.test', '.env.staging', '.envrc'];
for (const file of envFiles) {
  assert.strictEqual(isEnvironmentFile(file), true, `"${file}" should be detected as environment file`);
  const classification = classifyFileDetailed(file, file);
  assert.strictEqual(classification.classification, 'environment');
  assert.strictEqual(classification.reason, 'environment_file');
  console.log(`  ✓ Detected environment file: "${file}"`);
}

// 4. End-to-end scanner traversal and pruning test
console.log('\n[Test 4] Scanner directory pruning and file collection test:');
const tempBase = fs.mkdtempSync(path.join(os.tmpdir(), 'scan-test-'));

try {
  // Create sample repository structure
  // Application files (should be ACCEPTED)
  fs.writeFileSync(path.join(tempBase, 'app.py'), 'print("hello")', 'utf8');
  fs.writeFileSync(path.join(tempBase, 'testingService.py'), 'class TestingService: pass', 'utf8');
  fs.writeFileSync(path.join(tempBase, 'contest.js'), 'function contest() {}', 'utf8');

  // Skipped files in root
  fs.writeFileSync(path.join(tempBase, '.env'), 'SECRET=123', 'utf8');
  fs.writeFileSync(path.join(tempBase, 'app.test.js'), 'test("x", () => {});', 'utf8');
  fs.writeFileSync(path.join(tempBase, 'package-lock.json'), '{}', 'utf8');

  // Skipped directories (should NEVER be traversed)
  const venvDir = path.join(tempBase, '.venv', 'Lib', 'site-packages', 'sample_pkg');
  fs.mkdirSync(venvDir, { recursive: true });
  fs.writeFileSync(path.join(venvDir, 'heavy_lib.py'), '# should be pruned', 'utf8');
  fs.writeFileSync(path.join(venvDir, 'another.py'), '# should be pruned', 'utf8');

  const nodeModulesDir = path.join(tempBase, 'node_modules', 'express');
  fs.mkdirSync(nodeModulesDir, { recursive: true });
  fs.writeFileSync(path.join(nodeModulesDir, 'index.js'), '# should be pruned', 'utf8');

  const cacheDir = path.join(tempBase, '__pycache__');
  fs.mkdirSync(cacheDir, { recursive: true });
  fs.writeFileSync(path.join(cacheDir, 'app.cpython-310.pyc'), 'binary cache', 'utf8');

  const testsDir = path.join(tempBase, 'tests');
  fs.mkdirSync(testsDir, { recursive: true });
  fs.writeFileSync(path.join(testsDir, 'test_app.py'), 'def test_app(): pass', 'utf8');

  const config = {
    ignoreDirs: ['.git', '.venv', 'venv', 'env', 'ENV', 'node_modules', '__pycache__', 'dist', 'build'],
    supportedExtensions: ['.py', '.js', '.jsx', '.ts', '.tsx']
  };

  const scannedFiles = scanProject(tempBase, config, 'test-analysis-id');

  console.log(`\n  Scanned files count: ${scannedFiles.length}`);
  const relativeNames = scannedFiles.map(f => f.relativePath).sort();
  console.log('  Accepted files:', relativeNames);

  // Assertions
  assert.strictEqual(scannedFiles.length, 3, `Expected exactly 3 application files, got ${scannedFiles.length}`);
  assert.deepStrictEqual(relativeNames, ['app.py', 'contest.js', 'testingService.py']);

  // Check stats
  const stats = scannedFiles.stats;
  assert(stats, 'Expected stats on scannedFiles');
  assert.strictEqual(stats.analyzableSourceFiles, 3);
  assert(stats.directoriesSkippedByReason.virtual_environment >= 1, 'Expected virtual_environment directory skipped');
  assert(stats.directoriesSkippedByReason.dependency_directory >= 1, 'Expected dependency_directory skipped');
  assert(stats.directoriesSkippedByReason.cache_directory >= 1, 'Expected cache_directory skipped');
  assert(stats.directoriesSkippedByReason.test_directory >= 1, 'Expected test_directory skipped');

  assert(stats.filesSkippedByReason.environment_file >= 1, 'Expected environment_file skipped');
  assert(stats.filesSkippedByReason.test_file >= 1, 'Expected test_file skipped');
  assert(stats.filesSkippedByReason.configuration_file >= 1, 'Expected configuration_file skipped');

  console.log('\n  ✓ Scanner stats validation:');
  console.log('    - Total discovered files:', stats.totalFilesDiscovered);
  console.log('    - Analyzable source files:', stats.analyzableSourceFiles);
  console.log('    - Directories skipped by reason:', stats.directoriesSkippedByReason);
  console.log('    - Files skipped by reason:', stats.filesSkippedByReason);

  console.log('\n=== ALL UNIT TESTS PASSED SUCCESSFULLY ===');
} finally {
  // Cleanup temp dir
  try {
    fs.rmSync(tempBase, { recursive: true, force: true });
  } catch (e) {
    // Ignore cleanup errors on Windows
  }
}
