import assert from 'assert';
import { transformToFileLevelGraph } from '../src/utils/fileGraphTransformer.js';

console.log('=== RUNNING FILE GRAPH TRANSFORMER UNIT TESTS ===');

// Mock Code Map with duplicate filenames in different directories
const mockCodeMap = {
  project: { name: 'MultiLangApp', path: 'C:/Projects/MultiLangApp' },
  files: [
    {
      file: {
        relativePath: 'frontend/src/App.js',
        name: 'App.js',
        language: 'javascript',
        lineCount: 45
      },
      parse: { status: 'success' },
      imports: [
        { source: './Login.js', specifiers: ['Login'], importType: 'named', location: { startLine: 2 } },
        { source: 'react', specifiers: ['useState'], importType: 'named', location: { startLine: 1 } }
      ],
      exports: [{ exportType: 'default', name: 'App', location: { startLine: 40 } }],
      symbols: [{ name: 'App', type: 'component', location: { startLine: 5, endLine: 40 } }],
      classes: [],
      react: { components: [{ name: 'App' }], jsxElements: [{ name: 'Login' }, { name: 'div' }] },
      calls: [{ callee: 'renderLogin', location: { startLine: 15 } }],
      routes: [],
      databaseOperations: []
    },
    {
      file: {
        relativePath: 'backend/src/App.js',
        name: 'App.js',
        language: 'javascript',
        lineCount: 80
      },
      parse: { status: 'success' },
      imports: [
        { source: './routes.js', specifiers: ['router'], importType: 'default', location: { startLine: 3 } }
      ],
      exports: [{ exportType: 'named', name: 'server', location: { startLine: 75 } }],
      symbols: [{ name: 'startServer', type: 'function', location: { startLine: 10, endLine: 35 } }],
      classes: [],
      react: { components: [], jsxElements: [] },
      calls: [{ callee: 'app.listen', location: { startLine: 30 } }],
      routes: [{ method: 'GET', path: '/health', handler: 'healthCheck', location: { startLine: 20 } }],
      databaseOperations: [{ operation: 'query', callee: 'pool.query', location: { startLine: 25 } }]
    },
    {
      file: {
        relativePath: 'frontend/src/Login.js',
        name: 'Login.js',
        language: 'javascript',
        lineCount: 50
      },
      parse: { status: 'success' },
      imports: [
        { source: './api.js', specifiers: ['loginUser'], importType: 'named', location: { startLine: 2 } }
      ],
      exports: [{ exportType: 'default', name: 'Login' }],
      symbols: [{ name: 'handleLogin', type: 'function', location: { startLine: 8, endLine: 25 } }],
      classes: [],
      react: { components: [{ name: 'Login' }] },
      calls: [{ callee: 'loginUser', location: { startLine: 12 } }],
      routes: [],
      databaseOperations: []
    },
    {
      file: {
        relativePath: 'frontend/src/api.js',
        name: 'api.js',
        language: 'javascript',
        lineCount: 30
      },
      parse: { status: 'success' },
      imports: [],
      exports: [{ exportType: 'named', name: 'loginUser' }],
      symbols: [{ name: 'loginUser', type: 'function' }],
      classes: [],
      react: { components: [] },
      calls: [],
      routes: [],
      databaseOperations: []
    }
  ],
  relationships: [
    { from: 'frontend/src/App.js', to: 'frontend/src/Login.js', type: 'imports' },
    { from: 'frontend/src/Login.js', to: 'frontend/src/api.js', type: 'imports' }
  ]
};

// Mock rawGraph from backend
const mockRawGraph = {
  projectId: 'test-proj-123',
  projectName: 'MultiLangApp',
  projectPath: 'C:/Projects/MultiLangApp',
  nodes: [
    { id: 'file:frontend/src/App.js', type: 'file', name: 'App.js', filePath: 'frontend/src/App.js' },
    { id: 'file:backend/src/App.js', type: 'file', name: 'App.js', filePath: 'backend/src/App.js' },
    { id: 'file:frontend/src/Login.js', type: 'file', name: 'Login.js', filePath: 'frontend/src/Login.js' },
    { id: 'file:frontend/src/api.js', type: 'file', name: 'api.js', filePath: 'frontend/src/api.js' },
    { id: 'package:react', type: 'package', name: 'react' },
    // Entity nodes that should resolve to their containing file
    { id: 'function:handleLogin', type: 'function', name: 'handleLogin', filePath: 'frontend/src/Login.js' },
    { id: 'function:loginUser', type: 'function', name: 'loginUser', filePath: 'frontend/src/api.js' }
  ],
  edges: [
    // Cross-file entity call: handleLogin calls loginUser
    { source: 'function:handleLogin', target: 'function:loginUser', relationship: 'calls', evidence: { line: 12 } },
    // Direct file import
    { source: 'file:frontend/src/App.js', target: 'package:react', relationship: 'imports', evidence: { line: 1 } },
    // Self-edge: should be omitted!
    { source: 'file:frontend/src/App.js', target: 'file:frontend/src/App.js', relationship: 'calls' },
    // Contains edge: should be omitted from Level 1
    { source: 'file:frontend/src/Login.js', target: 'function:handleLogin', relationship: 'contains' }
  ],
  codeMap: mockCodeMap
};

const result = transformToFileLevelGraph(mockRawGraph);

console.log('[Test 1] Level-1 File Nodes Validation:');
console.log('  File count:', result.fileCount);
assert.strictEqual(result.fileCount, 4, `Expected 4 file nodes, got ${result.fileCount}`);

// Verify duplicate filenames are distinct
const frontendApp = result.nodes.find(n => n.id === 'frontend/src/App.js');
const backendApp = result.nodes.find(n => n.id === 'backend/src/App.js');
assert(frontendApp, 'frontend/src/App.js node must exist');
assert(backendApp, 'backend/src/App.js node must exist');
assert.strictEqual(frontendApp.name, 'App.js');
assert.strictEqual(backendApp.name, 'App.js');
assert.notStrictEqual(frontendApp.id, backendApp.id, 'Duplicate filenames must have distinct unique IDs');
console.log('  ✓ Duplicate filenames "frontend/src/App.js" and "backend/src/App.js" are distinctly preserved.');

console.log('\n[Test 2] Edge Resolution & Relationship Grouping:');
console.log('  Dependencies count:', result.depCount);
console.log('  Edges:', result.edges.map(e => `${e.source} --(${e.relationship})--> ${e.target}`));

// Verify edge from Login.js to api.js groups both 'imports' and 'calls'
const loginToApiEdge = result.edges.find(e => e.source === 'frontend/src/Login.js' && e.target === 'frontend/src/api.js');
assert(loginToApiEdge, 'Edge from Login.js to api.js must exist');
assert(loginToApiEdge.relationships.includes('imports'), 'Edge must include imports');
assert(loginToApiEdge.relationships.includes('calls'), 'Edge must include calls from handleLogin->loginUser');
console.log('  ✓ Cross-file entity call successfully mapped to file edge with grouped relationships:', loginToApiEdge.relationship);

// Verify self-edge is excluded
const selfEdge = result.edges.find(e => e.source === e.target);
assert.strictEqual(selfEdge, undefined, 'Self-edges must be omitted');
console.log('  ✓ Self-edges omitted.');

// Verify contains edge is excluded
const containsEdge = result.edges.find(e => e.relationship === 'contains' || e.relationships.includes('contains'));
assert.strictEqual(containsEdge, undefined, 'Contains relationships must not be file-level edges');
console.log('  ✓ "contains" relationships omitted.');

// Verify external package node included because it was imported
const reactPkgNode = result.nodes.find(n => n.id === 'pkg:react');
assert(reactPkgNode, 'Referenced external package "react" must be included in nodes');
console.log('  ✓ External package "react" included since it is imported by App.js');

console.log('\n[Test 3] O(1) Fast-Lookup Tables Validation:');
const lookup = result.lookup;
assert(lookup, 'Lookup object must be returned');

// Check fileById
const appArtifact = lookup.fileById.get('frontend/src/App.js');
assert(appArtifact, 'Lookup fileById must return App.js artifact');
assert.strictEqual(appArtifact.file.name, 'App.js');
assert.strictEqual(appArtifact.imports.length, 2);
console.log('  ✓ lookup.fileById("frontend/src/App.js") returned full Code Map artifact.');

// Check outgoingByFile
const loginOutgoing = lookup.outgoingByFile.get('frontend/src/Login.js');
assert(loginOutgoing, 'Login.js must have outgoing dependencies');
assert.strictEqual(loginOutgoing.length, 1);
assert.strictEqual(loginOutgoing[0].targetId, 'frontend/src/api.js');
console.log('  ✓ lookup.outgoingByFile("frontend/src/Login.js") returned:', loginOutgoing[0].targetName, loginOutgoing[0].relationships);

// Check incomingByFile
const apiIncoming = lookup.incomingByFile.get('frontend/src/api.js');
assert(apiIncoming, 'api.js must have incoming dependencies');
assert.strictEqual(apiIncoming.length, 1);
assert.strictEqual(apiIncoming[0].sourceId, 'frontend/src/Login.js');
console.log('  ✓ lookup.incomingByFile("frontend/src/api.js") returned caller:', apiIncoming[0].sourceName);

console.log('\n[Test 4] Dictionary-Keyed Code Map & Streamlined Properties Validation:');
const dictRawGraph = {
  projectId: 'dict-proj-456',
  projectName: 'MernApp',
  projectPath: 'C:/Projects/MernApp',
  nodes: [],
  edges: [],
  codeMap: {
    project: { name: 'MernApp', path: 'C:/Projects/MernApp' },
    files: {
      'backend/models/Payment.js': {
        language: 'javascript',
        lineCount: 49,
        dependencies: [
          { target: 'mongoose', type: 'external', kind: 'require', line: 1 }
        ],
        exports: [
          { name: 'Payment', type: 'model', line: 47 }
        ],
        declarations: [
          { name: 'PaymentSchema', type: 'schema', line: 5 },
          { name: 'Payment', type: 'model', line: 47 }
        ]
      },
      'backend/routes/paymentRoutes.js': {
        language: 'javascript',
        lineCount: 30,
        dependencies: [
          { target: '../models/Payment.js', type: 'internal', kind: 'require', line: 2 }
        ],
        routes: [
          { method: 'POST', path: '/api/payment/create', line: 10 }
        ]
      }
    },
    relationships: [
      {
        from: 'backend/routes/paymentRoutes.js',
        to: 'backend/models/Payment.js',
        type: 'imports',
        evidence: { line: 2 }
      },
      {
        from: 'backend/models/Payment.js',
        to: 'mongoose',
        type: 'imports',
        evidence: { line: 1 },
        external: true
      }
    ]
  }
};

const dictResult = transformToFileLevelGraph(dictRawGraph);
assert.strictEqual(dictResult.fileCount, 2, 'Must have exactly 2 source file nodes');
const paymentNode = dictResult.nodes.find(n => n.id === 'backend/models/Payment.js');
assert(paymentNode, 'Payment.js node must exist');
assert.strictEqual(paymentNode.name, 'Payment.js');
assert.strictEqual(paymentNode.label, 'Payment.js');
assert.strictEqual(paymentNode.lineCount, 49);

// Check external package mongoose
const mongoosePkg = dictResult.nodes.find(n => n.id === 'pkg:mongoose');
assert(mongoosePkg, 'mongoose package node must be created from external dependency');

// Check edge from paymentRoutes to Payment.js
const routeToPaymentEdge = dictResult.edges.find(e =>
  e.source === 'backend/routes/paymentRoutes.js' && e.target === 'backend/models/Payment.js'
);
assert(routeToPaymentEdge, 'Edge from paymentRoutes to Payment.js must exist');

// Check edge from Payment.js to mongoose
const paymentToMongooseEdge = dictResult.edges.find(e =>
  e.source === 'backend/models/Payment.js' && e.target === 'pkg:mongoose'
);
assert(paymentToMongooseEdge, 'Edge from Payment.js to mongoose package must exist');

// Check O(1) lookup
const paymentArtifact = dictResult.lookup.fileById.get('backend/models/Payment.js');
assert(paymentArtifact, 'lookup.fileById for Payment.js must return artifact');
assert.strictEqual(paymentArtifact.name, 'Payment.js');
assert.strictEqual(paymentArtifact.dependencies.length, 1);

const paymentOutgoing = dictResult.lookup.outgoingByFile.get('backend/models/Payment.js');
assert(paymentOutgoing && paymentOutgoing.length === 1, 'Payment.js must have 1 outgoing dependency (mongoose)');
assert.strictEqual(paymentOutgoing[0].targetId, 'pkg:mongoose');

console.log('  ✓ Dictionary-keyed Code Map transformed seamlessly with 2 files, 1 package, and O(1) lookups.');

console.log('\n=== ALL TRANSFORMER TESTS PASSED SUCCESSFULLY ===');
