const assert = require('assert');
const path = require('path');
const fs = require('fs');
const { extractInfo } = require('../src/extractor');
const { buildCodeMap, toStreamlinedFileEntry } = require('../src/codeMapBuilder');
const { parseFile } = require('../src/parser');

console.log('=== TEST: STREAMLINED DICTIONARY-KEYED CODE MAP & DEPENDENCIES ===\n');

async function runTests() {
  // Test 1: Real parsing with parseFile on a temporary CommonJS file
  console.log('1. Testing real AST parsing of CommonJS require:');
  const tempDir = path.join(__dirname, '..', 'scratch');
  if (!fs.existsSync(tempDir)) {
    fs.mkdirSync(tempDir, { recursive: true });
  }

  const tempPaymentJs = path.join(tempDir, 'Payment.js');
  fs.writeFileSync(tempPaymentJs, `const mongoose = require('mongoose');
const PaymentSchema = new mongoose.Schema({ amount: Number });
module.exports = mongoose.model('Payment', PaymentSchema);
`, 'utf8');

  const fileInfoJS = {
    absolutePath: tempPaymentJs,
    relativePath: 'backend/models/Payment.js',
    fileName: 'Payment.js',
    extension: '.js'
  };

  const parseResultJS = await parseFile(fileInfoJS);
  assert(parseResultJS.tree, 'Tree-sitter failed to parse Payment.js');
  console.log('  ✓ Tree-sitter successfully parsed Payment.js');

  const extractedJS = extractInfo(fileInfoJS, parseResultJS);
  console.log('  Parse status:', extractedJS.parse.status);
  console.log('  Extracted imports:', extractedJS.imports);

  assert(extractedJS.imports.length > 0, 'CommonJS require was not captured in imports');
  const reqImport = extractedJS.imports.find(i => i.source === 'mongoose');
  assert(reqImport, 'mongoose was not found in imports');
  assert.strictEqual(reqImport.importType, 'require', 'importType should be "require"');
  assert.strictEqual(reqImport.line, 1, 'Line number should be 1');
  console.log('  ✓ Extractor correctly detected require("mongoose") as dependency on line 1.');

  // Test 2: toStreamlinedFileEntry
  console.log('\n2. Testing toStreamlinedFileEntry:');
  const streamlined = toStreamlinedFileEntry(extractedJS);
  assert.strictEqual(streamlined.language, 'javascript');
  assert.strictEqual(streamlined.dependencies.length, 1);
  assert.strictEqual(streamlined.dependencies[0].target, 'mongoose');
  assert.strictEqual(streamlined.dependencies[0].kind, 'require');
  assert.strictEqual(streamlined.dependencies[0].type, 'external');
  assert.strictEqual(streamlined.dependencies[0].line, 1);

  // Check that duplicate/redundant fields are completely eliminated
  assert.strictEqual(streamlined.file, undefined, 'Duplicate file object MUST be omitted');
  assert.strictEqual(streamlined.parse, undefined, 'Duplicate parse object MUST be omitted');
  assert.strictEqual(streamlined.imports, undefined, 'Duplicate imports array MUST be omitted');
  assert.strictEqual(streamlined.calls, undefined, 'Noise calls MUST be omitted');
  assert.strictEqual(streamlined.react, undefined, 'Empty react field should be omitted');
  assert.strictEqual(streamlined.routes, undefined, 'Empty routes field should be omitted');
  assert.strictEqual(streamlined.databaseOperations, undefined, 'Empty databaseOperations should be omitted');
  console.log('  ✓ Streamlined file entry contains ONLY required fields (no duplicate file/parse/imports, no noise calls).');

  // Test 3: Python parsing
  console.log('\n3. Testing Python from-import & regular import:');
  const tempAppPy = path.join(tempDir, 'app.py');
  fs.writeFileSync(tempAppPy, `import os
from flask import Flask, jsonify

app = Flask(__name__)

@app.route('/api/status')
def status():
    return jsonify({"ok": True})
`, 'utf8');

  const fileInfoPy = {
    absolutePath: tempAppPy,
    relativePath: 'backend/app.py',
    fileName: 'app.py',
    extension: '.py'
  };

  const parseResultPy = await parseFile(fileInfoPy);
  assert(parseResultPy.tree, 'Tree-sitter failed to parse app.py');
  const extractedPy = extractInfo(fileInfoPy, parseResultPy);
  const streamlinedPy = toStreamlinedFileEntry(extractedPy);

  assert(streamlinedPy.dependencies.length >= 2, 'Should extract both os and flask dependencies');
  assert(streamlinedPy.routes.length === 1, 'Should extract route /api/status');
  assert.strictEqual(streamlinedPy.routes[0].path, '/api/status');
  console.log('  ✓ Python dependencies and routes correctly extracted.');

  // Test 4: buildCodeMap produces dictionary keyed by relativePath
  console.log('\n4. Testing buildCodeMap produces dictionary keyed by relativePath:');
  const projectInfo = { name: 'FullStackApp', path: 'C:/Projects/FullStackApp' };
  const codeMap = buildCodeMap(projectInfo, [extractedJS, extractedPy]);

  assert(!Array.isArray(codeMap.files), 'codeMap.files MUST be a dictionary, not an array');
  assert.strictEqual(typeof codeMap.files, 'object', 'codeMap.files must be an object');
  assert(codeMap.files['backend/models/Payment.js'], 'codeMap.files must have "backend/models/Payment.js"');
  assert(codeMap.files['backend/app.py'], 'codeMap.files must have "backend/app.py"');

  // Verify external relationships
  const mongooseRel = codeMap.relationships.find(r => r.from === 'backend/models/Payment.js' && r.to === 'mongoose');
  assert(mongooseRel, 'Relationship Payment.js -> mongoose must exist');
  assert.strictEqual(mongooseRel.external, true);

  const flaskRel = codeMap.relationships.find(r => r.from === 'backend/app.py' && r.to === 'flask');
  assert(flaskRel, 'Relationship app.py -> flask must exist');
  assert.strictEqual(flaskRel.external, true);
  console.log('  ✓ buildCodeMap produced dictionary-keyed codeMap.files with cross-file relationships.');

  // Cleanup temp files
  try {
    fs.unlinkSync(tempPaymentJs);
    fs.unlinkSync(tempAppPy);
  } catch (_) {}

  console.log('\n=== ALL AGENT TESTS PASSED SUCCESSFULLY! ===');
}

runTests().catch(err => {
  console.error('Test failed:', err);
  process.exit(1);
});
