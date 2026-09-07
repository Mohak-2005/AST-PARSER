const fs = require('fs');
const path = require('path');

const LOGS_DIR = path.resolve(__dirname, '../../logs');
const AGENT_LOG_FILE = path.join(LOGS_DIR, 'local-agent.log');
const ERRORS_LOG_FILE = path.join(LOGS_DIR, 'errors.log');

const MAX_SIZE_BYTES = (parseInt(process.env.LOG_MAX_SIZE_MB, 10) || 10) * 1024 * 1024;
const BACKUP_COUNT = parseInt(process.env.LOG_BACKUP_COUNT, 10) || 5;

function ensureLogsDir() {
  try {
    if (!fs.existsSync(LOGS_DIR)) {
      fs.mkdirSync(LOGS_DIR, { recursive: true });
    }
  } catch (err) {
    console.error('Failed to create logs directory:', err.message);
  }
}

function rotateFileIfNeeded(filePath) {
  try {
    if (!fs.existsSync(filePath)) return;
    const stats = fs.statSync(filePath);
    if (stats.size < MAX_SIZE_BYTES) return;

    for (let i = BACKUP_COUNT - 1; i >= 1; i--) {
      const oldFile = `${filePath}.${i}`;
      const newFile = `${filePath}.${i + 1}`;
      if (fs.existsSync(oldFile)) {
        if (i === BACKUP_COUNT - 1) {
          fs.unlinkSync(oldFile);
        } else {
          fs.renameSync(oldFile, newFile);
        }
      }
    }
    const firstBackup = `${filePath}.1`;
    if (fs.existsSync(firstBackup)) {
      try { fs.unlinkSync(firstBackup); } catch (e) {}
    }
    fs.renameSync(filePath, firstBackup);
  } catch (err) {
    console.error(`Log rotation failed for ${filePath}:`, err.message);
  }
}

function appendLog(filePath, line) {
  try {
    ensureLogsDir();
    rotateFileIfNeeded(filePath);
    fs.appendFileSync(filePath, line + '\n', 'utf8');
  } catch (err) {
    console.error(`Failed writing to log file ${filePath}:`, err.message);
  }
}

function formatTimestamp() {
  const now = new Date();
  const YYYY = now.getFullYear();
  const MM = String(now.getMonth() + 1).padStart(2, '0');
  const DD = String(now.getDate()).padStart(2, '0');
  const HH = String(now.getHours()).padStart(2, '0');
  const mm = String(now.getMinutes()).padStart(2, '0');
  const ss = String(now.getSeconds()).padStart(2, '0');
  const ms = String(now.getMilliseconds()).padStart(3, '0');
  return `${YYYY}-${MM}-${DD} ${HH}:${mm}:${ss}.${ms}`;
}

class AgentLogger {
  constructor(component = 'LOCAL_AGENT') {
    this.component = component;
    ensureLogsDir();
  }

  info(stage, message, extra = {}) {
    const ts = formatTimestamp();
    const aid = extra.analysisId ? ` [analysis_id=${extra.analysisId}]` : '';
    const extraStr = Object.entries(extra)
      .filter(([k]) => k !== 'analysisId')
      .map(([k, v]) => ` ${k}=${v}`)
      .join('');
    const line = `${ts} INFO  [${this.component}] [${stage}]${aid} ${message}${extraStr}`;
    console.log(line);
    appendLog(AGENT_LOG_FILE, line);
  }

  warn(stage, message, extra = {}) {
    const ts = formatTimestamp();
    const aid = extra.analysisId ? ` [analysis_id=${extra.analysisId}]` : '';
    const extraStr = Object.entries(extra)
      .filter(([k]) => k !== 'analysisId')
      .map(([k, v]) => ` ${k}=${v}`)
      .join('');
    const line = `${ts} WARN  [${this.component}] [${stage}]${aid} ${message}${extraStr}`;
    console.warn(line);
    appendLog(AGENT_LOG_FILE, line);
  }

  error(stage, message, error = null, extra = {}) {
    const ts = formatTimestamp();
    const aid = extra.analysisId ? ` [analysis_id=${extra.analysisId}]` : '';
    const errType = error ? error.name || 'Error' : 'Error';
    const errMsg = error ? error.message : message;
    const stack = error && error.stack ? `\nstack=${error.stack}` : '';

    const extraStr = Object.entries(extra)
      .filter(([k]) => k !== 'analysisId')
      .map(([k, v]) => ` ${k}=${v}`)
      .join('');

    const logLine = `${ts} ERROR [${this.component}] [${stage}]${aid} ${message} error_type=${errType} error_message="${errMsg}"${extraStr}${stack}`;
    console.error(logLine);
    appendLog(AGENT_LOG_FILE, logLine);

    // Also append to errors.log
    const errorEntry = `${ts} [${this.component}] [${stage}]${aid} error_type=${errType} error_message="${errMsg}"${extraStr}${stack}`;
    appendLog(ERRORS_LOG_FILE, errorEntry);
  }

  stage(stageName, status, extra = {}) {
    const ts = formatTimestamp();
    const aid = extra.analysisId ? ` analysis_id=${extra.analysisId}` : '';
    const dur = extra.duration_ms !== undefined ? ` duration_ms=${extra.duration_ms}` : '';
    const details = Object.entries(extra)
      .filter(([k]) => !['analysisId', 'duration_ms', 'error'].includes(k))
      .map(([k, v]) => ` ${k}=${v}`)
      .join('');

    const level = status === 'FAILURE' ? 'ERROR' : 'INFO';
    const line = `${ts} ${level.padEnd(5)} [${this.component}] [${stageName}] ${status}${aid}${dur}${details}`;
    
    if (status === 'FAILURE') {
      console.error(line);
      appendLog(AGENT_LOG_FILE, line);
      if (extra.error) {
        this.error(stageName, `Stage ${stageName} failed`, extra.error, extra);
      } else {
        appendLog(ERRORS_LOG_FILE, line);
      }
    } else {
      console.log(line);
      appendLog(AGENT_LOG_FILE, line);
    }
  }
}

const defaultLogger = new AgentLogger('LOCAL_AGENT');
module.exports = { AgentLogger, logger: defaultLogger };
