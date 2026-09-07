const axios = require('axios');
const { logger } = require('./logger');

async function sendCodeMap(codeMap, config) {
  const analysisId = codeMap.analysis_id || codeMap.project?.analysis_id;
  const url = `${config.backendUrl}/api/analyze`;
  const t0 = Date.now();

  logger.info('BACKEND_REQUEST', `Sending Code Map to ${url}`, { analysisId, file_count: codeMap.files?.length || 0 });
  logger.stage('BACKEND_REQUEST', 'START', { analysisId, file_count: codeMap.files?.length || 0 });

  try {
    const response = await axios.post(url, codeMap, {
      headers: {
        'Content-Type': 'application/json',
        'X-Analysis-Id': analysisId || ''
      },
      maxBodyLength: Infinity,
      maxContentLength: Infinity,
      timeout: (config.analysisTimeoutSeconds || 3600) * 1000
    });

    const duration_ms = Date.now() - t0;
    logger.stage('BACKEND_REQUEST', 'SUCCESS', {
      analysisId,
      duration_ms,
      status: response.status,
      projectId: response.data?.projectId
    });

    return {
      projectId: response.data.projectId,
      graph: response.data.graph
    };
  } catch (error) {
    const duration_ms = Date.now() - t0;
    let errorMsg = error.message;

    if (error.code === 'ECONNABORTED' || (error.message && error.message.toLowerCase().includes('timeout'))) {
      errorMsg = `Analysis request timed out after ${config.analysisTimeoutSeconds || 3600}s waiting for backend. Check backend logs for the failing stage.`;
    } else if (error.response) {
      let detail = error.response.data;
      if (typeof detail === 'object' && detail !== null) {
        if (typeof detail.error === 'string') {
          detail = detail.error;
        } else if (typeof detail.detail === 'string') {
          detail = detail.detail;
        } else if (detail.detail && typeof detail.detail === 'object') {
          detail = detail.detail.error || detail.detail.detail || JSON.stringify(detail.detail);
        } else {
          detail = JSON.stringify(detail);
        }
      }
      errorMsg = `Backend error (${error.response.status}): ${detail}`;
    } else if (error.request) {
      errorMsg = `Backend connection error: ${error.message || 'No response received from backend'}`;
    }

    logger.stage('BACKEND_REQUEST', 'FAILURE', {
      analysisId,
      duration_ms,
      error_type: error.name || 'AxiosError',
      error_message: errorMsg,
      error
    });

    throw new Error(errorMsg);
  }
}

module.exports = { sendCodeMap };
