import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = path.resolve(__dirname, '../..');
const PROJECT_ROOT = path.resolve(WEB_ROOT, '..');

const SETTINGS_PATH = path.join(WEB_ROOT, 'settings.json');
const DEFAULT_CONFIG_PATH = path.join(WEB_ROOT, 'config.default.json');
const RUNTIME_DIR = path.join(WEB_ROOT, 'runtime');
const RUNTIME_CONFIG_PATH = path.join(RUNTIME_DIR, 'config.toml');
const SRC_CONFIG_PATH = path.join(PROJECT_ROOT, 'src', 'config.toml');

const PROVIDER_SECTIONS = ['itext2kg_concepts', 'itext2kg_graph', 'refiner'];

/**
 * Manages web application settings and runtime Python config generation.
 */
export class ConfigManager {
  constructor() {
    this.settings = null;
  }

  async init() {
    await fs.mkdir(RUNTIME_DIR, { recursive: true });
    this.settings = await this.loadSettings();
    await this.syncRuntimeConfig();
    return this.settings;
  }

  async loadSettings() {
    try {
      const raw = await fs.readFile(SETTINGS_PATH, 'utf8');
      return JSON.parse(raw);
    } catch {
      const raw = await fs.readFile(DEFAULT_CONFIG_PATH, 'utf8');
      const defaults = JSON.parse(raw);
      await this.saveSettings(defaults);
      return defaults;
    }
  }

  async saveSettings(nextSettings) {
    this.settings = nextSettings;
    await fs.writeFile(SETTINGS_PATH, JSON.stringify(nextSettings, null, 2), 'utf8');
    await this.syncRuntimeConfig();
  }

  getProjectRoot() {
    return PROJECT_ROOT;
  }

  getWebRoot() {
    return WEB_ROOT;
  }

  resolveProjectPath(relativePath) {
    return path.join(PROJECT_ROOT, relativePath);
  }

  getPythonExecutable() {
    const venvRelative = this.settings?.paths?.pythonVenv ?? '../.venv';
    const venvRoot = path.resolve(WEB_ROOT, venvRelative);
    const winPython = path.join(venvRoot, 'Scripts', 'python.exe');
    const unixPython = path.join(venvRoot, 'bin', 'python');
    return process.platform === 'win32' ? winPython : unixPython;
  }

  getMode() {
    return this.settings?.mode ?? 'online';
  }

  getProviderForMode(mode = this.getMode()) {
    const providers = this.settings?.providers ?? {};
    return mode === 'offline' ? providers.offline ?? 'local_transformers' : providers.online ?? 'openrouter';
  }

  async readSourceConfigText() {
    return fs.readFile(SRC_CONFIG_PATH, 'utf8');
  }

  patchProviderInToml(tomlText, provider) {
    let result = tomlText;
    for (const section of PROVIDER_SECTIONS) {
      const sectionRegex = new RegExp(`(\\[${section}\\][\\s\\S]*?)(provider\\s*=\\s*")([^"]+)(")`, 'm');
      if (sectionRegex.test(result)) {
        result = result.replace(sectionRegex, `$1$2${provider}$4`);
      }
    }
    return this.normalizeLocalPaths(result);
  }

  normalizeLocalPaths(tomlText) {
    const normalizedRoot = PROJECT_ROOT.replace(/\\/g, '/');
    return tomlText
      .replace(/C:\/NORNIKEL2\/k2-18/gi, normalizedRoot)
      .replace(/C:\\NORNIKEL2\\k2-18/gi, normalizedRoot.replace(/\//g, '\\'));
  }

  async syncRuntimeConfig() {
    const sourceToml = await this.readSourceConfigText();
    const provider = this.getProviderForMode();
    const patched = this.patchProviderInToml(sourceToml, provider);
    await fs.writeFile(RUNTIME_CONFIG_PATH, patched, 'utf8');
  }

  async setMode(mode) {
    if (!['online', 'offline'].includes(mode)) {
      throw new Error(`Unsupported mode: ${mode}`);
    }
    const next = { ...this.settings, mode };
    await this.saveSettings(next);
    return next;
  }

  async getStatus() {
    const sourceToml = await this.readSourceConfigText();
    const providerMatch = sourceToml.match(/\[itext2kg_concepts\][\s\S]*?provider\s*=\s*"([^"]+)"/m);
    const sourceProvider = providerMatch?.[1] ?? 'unknown';
    const runtimeToml = await fs.readFile(RUNTIME_CONFIG_PATH, 'utf8').catch(() => '');
    const runtimeProviderMatch = runtimeToml.match(/\[itext2kg_concepts\][\s\S]*?provider\s*=\s*"([^"]+)"/m);

    return {
      mode: this.getMode(),
      activeProvider: runtimeProviderMatch?.[1] ?? this.getProviderForMode(),
      sourceProvider,
      pythonExecutable: this.getPythonExecutable(),
      runtimeConfigPath: RUNTIME_CONFIG_PATH,
      projectRoot: PROJECT_ROOT,
    };
  }
}

export const configManager = new ConfigManager();
