import fs from 'node:fs/promises';
import path from 'node:path';
import { configManager } from './configManager.js';

const LOG_PATH = path.join(configManager.getWebRoot(), 'runtime', 'audit.log');

/**
 * Append-only audit logger for user interactions.
 */
export class AuditLog {
  async init() {
    await fs.mkdir(path.dirname(LOG_PATH), { recursive: true });
    try {
      await fs.access(LOG_PATH);
    } catch {
      await fs.writeFile(LOG_PATH, '', 'utf8');
    }
  }

  async record(event, details = {}) {
    const line = JSON.stringify({
      timestamp: new Date().toISOString(),
      event,
      ...details,
    });
    await fs.appendFile(LOG_PATH, `${line}\n`, 'utf8');
  }

  async tail(limit = 100) {
    try {
      const raw = await fs.readFile(LOG_PATH, 'utf8');
      return raw
        .trim()
        .split('\n')
        .filter(Boolean)
        .slice(-limit)
        .map((line) => JSON.parse(line));
    } catch {
      return [];
    }
  }
}

export const auditLog = new AuditLog();
