/**
 * Tree-sitter parser using web-tree-sitter (WASM).
 * Works dynamically across all available programming language grammars.
 */

const fs = require('fs');
const path = require('path');
const { registry } = require('./languageRegistry');

let Parser = null;
let Language = null;
let parserInitialized = false;

const grammarCache = new Map(); // grammarKey → Language object

/**
 * Initialize the WASM runtime once.
 * Must be called before any Language.load() or parser.parse().
 */
async function initParser() {
  if (parserInitialized) return;

  const wts = require('web-tree-sitter');
  Parser = wts.Parser;
  Language = wts.Language;

  const mainWasm = path.join(__dirname, '..', 'node_modules', 'web-tree-sitter', 'tree-sitter.wasm');

  await Parser.init({
    locateFile(filename) {
      if (filename === 'tree-sitter.wasm') return mainWasm;
      return filename;
    }
  });

  parserInitialized = true;
  console.log('[Parser] web-tree-sitter WASM runtime initialized');
}

/**
 * Load and cache a grammar dynamically for the given language identifier or resolution info.
 * Grammar objects are reused across all files of the same grammarKey.
 */
async function loadGrammar(resolved) {
  if (!resolved || !resolved.grammarKey || !resolved.wasmPath) {
    return null;
  }

  const { grammarKey, wasmPath, language } = resolved;

  if (grammarCache.has(grammarKey)) {
    return grammarCache.get(grammarKey);
  }

  if (!fs.existsSync(wasmPath)) {
    console.warn(`[Parser] Grammar WASM not found for ${language} (${grammarKey}): ${wasmPath}`);
    return null;
  }

  try {
    const lang = await Language.load(wasmPath);
    grammarCache.set(grammarKey, lang);
    console.log(`[Parser] Loaded dynamic grammar: ${grammarKey} from ${path.basename(wasmPath)}`);
    return lang;
  } catch (err) {
    console.error(`[Parser] Failed to load grammar for ${language}:`, err.message);
    return null;
  }
}

/**
 * Parse a single source file using its dynamically resolved Tree-sitter grammar.
 * Always returns a result object — never throws.
 *
 * @param {Object} fileInfo - { absolutePath, relativePath, fileName, extension }
 * @returns {Promise<{ tree, sourceCode, language, hasErrors: boolean, errors: string[] }>}
 */
async function parseFile(fileInfo) {
  const resolved = registry.resolveLanguage(fileInfo.extension);

  if (!resolved) {
    return {
      tree: null,
      sourceCode: null,
      language: null,
      hasErrors: true,
      errors: [`No Tree-sitter grammar available for extension: ${fileInfo.extension}`]
    };
  }

  try {
    await initParser();

    const sourceCode = fs.readFileSync(fileInfo.absolutePath, 'utf8');
    const grammar = await loadGrammar(resolved);

    if (!grammar) {
      return {
        tree: null,
        sourceCode,
        language: resolved.language,
        hasErrors: true,
        errors: [`Grammar WASM unavailable for language: ${resolved.language}`]
      };
    }

    const parser = new Parser();
    parser.setLanguage(grammar);
    const tree = parser.parse(sourceCode);

    return {
      tree,
      sourceCode,
      language: resolved.language,
      hasErrors: tree.rootNode.hasError,
      errors: tree.rootNode.hasError ? ['Syntax errors or parse recovery present in AST'] : []
    };
  } catch (err) {
    return {
      tree: null,
      sourceCode: null,
      language: resolved ? resolved.language : null,
      hasErrors: true,
      errors: [err.message]
    };
  }
}

module.exports = {
  parseFile,
  initParser,
  loadGrammar
};
