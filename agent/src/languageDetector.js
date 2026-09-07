const { registry } = require('./languageRegistry');

/**
 * Dynamically detects language identifier from file extension using the language registry.
 * @param {string} extension - e.g. '.java', '.py', '.js'
 * @returns {string | null}
 */
function detectLanguage(extension) {
  const resolved = registry.resolveLanguage(extension);
  return resolved ? resolved.language : null;
}

/**
 * Returns path to the WASM grammar file for a given language.
 */
function getWasmPath(language) {
  return registry.getWasmPath(language);
}

/**
 * Maps language to the canonical grammar name used as a cache key.
 */
function getGrammarKey(language) {
  return registry.getGrammarKey(language);
}

module.exports = {
  detectLanguage,
  getWasmPath,
  getGrammarKey,
  registry
};
