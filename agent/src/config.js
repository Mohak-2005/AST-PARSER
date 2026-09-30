const path = require('path');
require('dotenv').config({ path: path.resolve(__dirname, '../.env') });
const { registry } = require('./languageRegistry');

module.exports = {
  port: parseInt(process.env.PORT || '3001', 10),
  backendUrl: process.env.BACKEND_URL || 'http://localhost:5000',
  analysisOutputDir: path.resolve(process.env.ANALYSIS_OUTPUT_DIR || path.join(__dirname, '../../analysis_output')),
  ignoreDirs: (process.env.IGNORE_DIRS || '.git,.venv,venv,env,ENV,node_modules,__pycache__,.pytest_cache,.mypy_cache,.ruff_cache,.cache,dist,build,coverage,target,out,bin,obj,.next,.nuxt,.turbo,.parcel-cache,vendor,.nyc_output').split(','),
  analysisTimeoutSeconds: parseInt(process.env.ANALYSIS_TIMEOUT_SECONDS || '3600', 10),
  get supportedExtensions() {
    return registry.getSupportedExtensions();
  }
};
