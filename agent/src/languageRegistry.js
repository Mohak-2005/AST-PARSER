const fs = require('fs');
const path = require('path');

const WASM_OUT_DIR = path.join(__dirname, '..', 'node_modules', 'tree-sitter-wasms', 'out');
const WASM_LOCAL_DIR = path.join(__dirname, '..', 'wasm');

/**
 * Known language grammar definitions mapping language identifiers,
 * primary grammar WASM filenames, and file extensions.
 */
const DEFAULT_LANGUAGE_DEFINITIONS = [
  { language: 'java', grammarWasm: 'tree-sitter-java.wasm', extensions: ['.java'] },
  { language: 'python', grammarWasm: 'tree-sitter-python.wasm', extensions: ['.py', '.pyw'] },
  { language: 'javascript', grammarWasm: 'tree-sitter-javascript.wasm', extensions: ['.js', '.jsx', '.mjs', '.cjs'] },
  { language: 'typescript', grammarWasm: 'tree-sitter-typescript.wasm', extensions: ['.ts', '.mts', '.cts'] },
  { language: 'tsx', grammarWasm: 'tree-sitter-tsx.wasm', extensions: ['.tsx'] },
  { language: 'c', grammarWasm: 'tree-sitter-c.wasm', extensions: ['.c', '.h'] },
  { language: 'cpp', grammarWasm: 'tree-sitter-cpp.wasm', extensions: ['.cpp', '.cc', '.cxx', '.c++', '.hpp', '.hh', '.hxx'] },
  { language: 'c_sharp', grammarWasm: 'tree-sitter-c_sharp.wasm', extensions: ['.cs'] },
  { language: 'go', grammarWasm: 'tree-sitter-go.wasm', extensions: ['.go'] },
  { language: 'rust', grammarWasm: 'tree-sitter-rust.wasm', extensions: ['.rs'] },
  { language: 'ruby', grammarWasm: 'tree-sitter-ruby.wasm', extensions: ['.rb'] },
  { language: 'php', grammarWasm: 'tree-sitter-php.wasm', extensions: ['.php'] },
  { language: 'kotlin', grammarWasm: 'tree-sitter-kotlin.wasm', extensions: ['.kt', '.kts'] },
  { language: 'swift', grammarWasm: 'tree-sitter-swift.wasm', extensions: ['.swift'] },
  { language: 'scala', grammarWasm: 'tree-sitter-scala.wasm', extensions: ['.scala', '.sc'] },
  { language: 'dart', grammarWasm: 'tree-sitter-dart.wasm', extensions: ['.dart'] },
  { language: 'lua', grammarWasm: 'tree-sitter-lua.wasm', extensions: ['.lua'] },
  { language: 'zig', grammarWasm: 'tree-sitter-zig.wasm', extensions: ['.zig'] },
  { language: 'bash', grammarWasm: 'tree-sitter-bash.wasm', extensions: ['.sh', '.bash'] },
  { language: 'solidity', grammarWasm: 'tree-sitter-solidity.wasm', extensions: ['.sol'] },
  { language: 'elixir', grammarWasm: 'tree-sitter-elixir.wasm', extensions: ['.ex', '.exs'] },
  { language: 'elm', grammarWasm: 'tree-sitter-elm.wasm', extensions: ['.elm'] },
  { language: 'objc', grammarWasm: 'tree-sitter-objc.wasm', extensions: ['.m', '.mm'] }
];

class LanguageRegistry {
  constructor() {
    this.extensionMap = new Map(); // ext -> { language, grammarKey, wasmPath }
    this.languageMap = new Map();  // language -> { grammarKey, wasmPath, extensions: Set }
    this._initialized = false;
    this._init();
  }

  /**
   * Discovers and resolves all available WASM grammars from disk.
   */
  _init() {
    if (this._initialized) return;

    for (const def of DEFAULT_LANGUAGE_DEFINITIONS) {
      const wasmPath = this._locateWasm(def.grammarWasm);
      if (wasmPath) {
        this._register(def.language, def.language, wasmPath, def.extensions);
      }
    }

    this._initialized = true;
  }

  /**
   * Locates a WASM file in local wasm/ directory or tree-sitter-wasms/out/.
   */
  _locateWasm(wasmFilename) {
    const localPath = path.join(WASM_LOCAL_DIR, wasmFilename);
    if (fs.existsSync(localPath)) return localPath;

    const outPath = path.join(WASM_OUT_DIR, wasmFilename);
    if (fs.existsSync(outPath)) return outPath;

    return null;
  }

  /**
   * Internal helper to register a grammar with extension associations.
   */
  _register(language, grammarKey, wasmPath, extensions) {
    const entry = {
      language,
      grammarKey,
      wasmPath,
      extensions: new Set(extensions.map(e => e.toLowerCase()))
    };

    this.languageMap.set(language, entry);

    for (const ext of extensions) {
      const normalizedExt = ext.toLowerCase();
      this.extensionMap.set(normalizedExt, {
        language,
        grammarKey,
        wasmPath
      });
    }
  }

  /**
   * Dynamically register a new or custom grammar.
   */
  registerGrammar(extension, language, wasmPath, grammarKey = null) {
    const normExt = (extension.startsWith('.') ? extension : `.${extension}`).toLowerCase();
    const gKey = grammarKey || language;
    
    if (!fs.existsSync(wasmPath)) {
      throw new Error(`Grammar WASM file does not exist at: ${wasmPath}`);
    }

    this.extensionMap.set(normExt, {
      language,
      grammarKey: gKey,
      wasmPath
    });

    if (!this.languageMap.has(language)) {
      this.languageMap.set(language, {
        language,
        grammarKey: gKey,
        wasmPath,
        extensions: new Set([normExt])
      });
    } else {
      this.languageMap.get(language).extensions.add(normExt);
    }
  }

  /**
   * Resolves language and grammar details for a given file extension.
   * @param {string} extension - e.g. '.java', '.py', '.js'
   * @returns {{ language: string, grammarKey: string, wasmPath: string } | null}
   */
  resolveLanguage(extension) {
    if (!extension) return null;
    const normExt = extension.toLowerCase();
    return this.extensionMap.get(normExt) || null;
  }

  /**
   * Returns whether a file extension has an available Tree-sitter grammar.
   */
  isSupported(extension) {
    if (!extension) return false;
    return this.extensionMap.has(extension.toLowerCase());
  }

  /**
   * Returns a list of all supported file extensions with active grammars.
   */
  getSupportedExtensions() {
    return Array.from(this.extensionMap.keys());
  }

  /**
   * Returns list of all available languages and their grammar paths.
   */
  getAvailableLanguages() {
    return Array.from(this.languageMap.values()).map(entry => ({
      language: entry.language,
      grammarKey: entry.grammarKey,
      wasmPath: entry.wasmPath,
      extensions: Array.from(entry.extensions)
    }));
  }

  /**
   * Returns the WASM path for a language.
   */
  getWasmPath(language) {
    const entry = this.languageMap.get(language);
    return entry ? entry.wasmPath : null;
  }

  /**
   * Returns the grammar key for a language.
   */
  getGrammarKey(language) {
    const entry = this.languageMap.get(language);
    return entry ? entry.grammarKey : language;
  }
}

// Singleton registry instance
const registry = new LanguageRegistry();

module.exports = {
  registry,
  LanguageRegistry
};
