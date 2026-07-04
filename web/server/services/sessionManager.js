import fs from 'node:fs/promises';
import path from 'node:path';
import { v4 as uuidv4 } from 'uuid';
import { configManager } from './configManager.js';

const SESSIONS_DIR = path.join(configManager.getWebRoot(), 'runtime', 'sessions');

/**
 * Lightweight session store for restoring UI state.
 */
export class SessionManager {
  async init() {
    await fs.mkdir(SESSIONS_DIR, { recursive: true });
  }

  sessionPath(sessionId) {
    return path.join(SESSIONS_DIR, `${sessionId}.json`);
  }

  async create(initialState = {}) {
    const session = {
      id: uuidv4(),
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      state: initialState,
    };
    await fs.writeFile(this.sessionPath(session.id), JSON.stringify(session, null, 2), 'utf8');
    return session;
  }

  async get(sessionId) {
    try {
      const raw = await fs.readFile(this.sessionPath(sessionId), 'utf8');
      return JSON.parse(raw);
    } catch {
      return null;
    }
  }

  async update(sessionId, statePatch) {
    const session = await this.get(sessionId);
    if (!session) return null;
    session.state = { ...session.state, ...statePatch };
    session.updatedAt = new Date().toISOString();
    await fs.writeFile(this.sessionPath(session.id), JSON.stringify(session, null, 2), 'utf8');
    return session;
  }
}

export const sessionManager = new SessionManager();
