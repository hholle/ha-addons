/**
 * Simple stdout logger with level filtering.
 *
 * Level is read from the LOG_LEVEL environment variable (default: 'info').
 * All output goes to stdout so s6-overlay / Docker can capture it.
 *
 * Levels in ascending verbosity:
 *   error → warn → info → http → verbose → debug → silly
 *
 * Usage:
 *   import log from './logger.js';
 *   log.info('Starting...');
 *   log.debug('payload:', JSON.stringify(data));
 */

const LEVELS = ['error', 'warn', 'info', 'http', 'verbose', 'debug', 'silly'];
const currentLevel = LEVELS.indexOf(process.env.LOG_LEVEL || 'info');

const logger = {};
LEVELS.forEach((level, i) => {
    logger[level] = (...args) => {
        if (i <= currentLevel) console.log(`[${level}]`, ...args);
    };
});

export default logger;
