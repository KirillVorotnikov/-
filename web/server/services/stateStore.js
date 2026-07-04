import fs from 'node:fs/promises';
import path from 'node:path';
import { configManager } from './configManager.js';

const STATE_PATH = path.join(configManager.getWebRoot(), 'runtime', 'app_state.json');

const DEFAULT_STATE = {
  activeJobId: null,
  lastPipelineStage: null,
  selectedGraphPath: null,
  lastDiagnosticsReportId: null,
  lastAuditReportPath: null,
  mode: 'online',
  provider: null,
  hypothesesCache: [],
  preferences: {
    theme: 'dark',
    locale: 'ru',
  },
  artifactsMetadata: {
    lastProvider: null,
    lastGeneratedAt: null,
  },
  integrationMode: 'new',
};

export class StateStore {
  constructor() {
    this.state = null;
  }

  async init() {
    try {
      const raw = await fs.readFile(STATE_PATH, 'utf8');
      this.state = { ...DEFAULT_STATE, ...JSON.parse(raw) };
    } catch {
      this.state = { ...DEFAULT_STATE };
      await this.persist();
    }
    return this.state;
  }

  async persist() {
    await fs.mkdir(path.dirname(STATE_PATH), { recursive: true });
    await fs.writeFile(STATE_PATH, JSON.stringify(this.state, null, 2), 'utf8');
  }

  get() {
    return this.state;
  }

  async update(patch) {
    this.state = {
      ...this.state,
      ...patch,
      preferences: { ...this.state.preferences, ...(patch.preferences ?? {}) },
      artifactsMetadata: { ...this.state.artifactsMetadata, ...(patch.artifactsMetadata ?? {}) },
    };
    await this.persist();
    return this.state;
  }

  async recordPipelineComplete(job) {
    await this.update({
      activeJobId: null,
      lastPipelineStage: job.stage,
      artifactsMetadata: {
        lastProvider: configManager.getProviderForMode(),
        lastGeneratedAt: new Date().toISOString(),
      },
    });
  }

  async cacheHypotheses(report) {
    await this.update({
      hypothesesCache: report.hypotheses ?? [],
    });
  }

  needsRegenerationWarning(newMode) {
    const lastProvider = this.state.artifactsMetadata?.lastProvider;
    if (!lastProvider) return false;
    const newProvider = configManager.getProviderForMode(newMode);
    return lastProvider !== newProvider;
  }
}

export const stateStore = new StateStore();
