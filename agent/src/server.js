const express = require('express');
const cors = require('cors');
const fs = require('fs');
const config = require('./config');
const { runAnalysis } = require('./analysisRunner');
const { logger } = require('./logger');

const app = express();
app.use(cors());
app.use(express.json());

const statusTracker = {
  stage: 'Idle',
  progress: null,
  projectId: null,
  analysisId: null,
  error: null,
  analyzing: false
};

app.get('/agent/health', (req, res) => {
  res.json({ status: 'ok', version: '1.0.0' });
});

app.get('/agent/status', (req, res) => {
  res.json(statusTracker);
});

app.post('/agent/analyze', (req, res) => {
  const { projectPath } = req.body;

  if (!projectPath) {
    logger.warn('PROJECT_SELECTION', 'Rejected request: projectPath is required');
    return res.status(400).json({ error: 'projectPath is required' });
  }

  if (statusTracker.analyzing) {
    logger.warn('PROJECT_SELECTION', 'Rejected request: analysis already running', { activeAnalysisId: statusTracker.analysisId });
    return res.status(409).json({ error: 'An analysis is already running' });
  }

  if (!fs.existsSync(projectPath)) {
    logger.warn('PROJECT_SELECTION', `Rejected request: path does not exist: ${projectPath}`);
    return res.status(404).json({ error: 'Project path does not exist' });
  }

  logger.info('PROJECT_SELECTION', `Received analysis request for project: ${projectPath}`);

  // Start analysis asynchronously
  runAnalysis(projectPath, statusTracker, config);

  res.json({
    message: 'Analysis started',
    projectPath
  });
});

const server = app.listen(config.port, '0.0.0.0', () => {
  logger.info('STARTUP', `Local Agent listening on port ${config.port}`);
  logger.info('STARTUP', `Analysis output directory: ${config.analysisOutputDir}`);
});

process.on('SIGTERM', () => {
  logger.info('SHUTDOWN', 'Local Agent received SIGTERM. Shutting down gracefully.');
  server.close(() => {
    process.exit(0);
  });
});

process.on('SIGINT', () => {
  logger.info('SHUTDOWN', 'Local Agent received SIGINT. Shutting down.');
  server.close(() => {
    process.exit(0);
  });
});
