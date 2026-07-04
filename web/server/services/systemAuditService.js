import fs from 'node:fs/promises';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { configManager } from './configManager.js';
import { fileExists } from './graphLoader.js';

const PYTHON_PACKAGES = [
  'transformers', 'torch', 'httpx', 'sentence_transformers', 'faiss',
  'numpy', 'dotenv', 'bs4', 'unidecode', 'tomli', 'networkx',
  'community', 'minify_html', 'jinja2',
];

const REQUIRED_DIRS = [
  'data/raw', 'data/staging', 'data/out', 'logs',
  'viz/data/in', 'viz/data/out', 'viz/logs',
  'viz/templates', 'viz/templates/viewer',
  'viz/static', 'viz/static/viewer', 'viz/vendor',
  'src/prompts', 'src/schemas',
];

const GRAPH2HTML_VENDOR = [
  'viz/vendor/cytoscape.min.js',
  'viz/vendor/cytoscape-cose-bilkent.js',
  'viz/vendor/cytoscape-navigator.js',
  'viz/vendor/cytoscape.js-navigator.css',
  'viz/vendor/marked.min.js',
  'viz/vendor/highlight.min.js',
  'viz/vendor/github-dark.min.css',
];

const GRAPH2VIEWER_FILES = [
  'viz/templates/viewer/index.html',
  'viz/static/viewer/viewer_core.js',
  'viz/static/viewer/node_explorer.js',
  'viz/static/viewer/edge_inspector.js',
  'viz/static/viewer/search_filter.js',
  'viz/static/viewer/formatters.js',
  'viz/static/viewer/navigation_history.js',
];

function checkRow(category, name, status, message, details = {}) {
  return { category, name, status, message, details };
}

function runCommand(cmd, args, cwd) {
  return new Promise((resolve) => {
    const child = spawn(cmd, args, { cwd, shell: false });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (d) => { stdout += d.toString(); });
    child.stderr.on('data', (d) => { stderr += d.toString(); });
    child.on('close', (code) => resolve({ code, stdout, stderr }));
    child.on('error', (error) => resolve({ code: -1, stdout, stderr: error.message }));
  });
}

async function checkPythonEnvironment() {
  const rows = [];
  const python = configManager.getPythonExecutable();
  const venvExists = await fileExists(python);

  rows.push(checkRow('python', 'venv', venvExists ? 'pass' : 'fail', venvExists ? 'Virtual environment found' : 'Virtual environment not found', { python }));

  const versionResult = await runCommand(python, ['--version'], configManager.getProjectRoot());
  const versionMatch = (versionResult.stdout || versionResult.stderr).match(/(\d+)\.(\d+)/);
  const major = versionMatch ? Number(versionMatch[1]) : 0;
  const minor = versionMatch ? Number(versionMatch[2]) : 0;
  const versionOk = major > 3 || (major === 3 && minor >= 10);
  rows.push(checkRow('python', 'version', versionOk ? 'pass' : 'fail', versionResult.stdout.trim() || versionResult.stderr.trim(), { major, minor }));

  for (const pkg of PYTHON_PACKAGES) {
    const importName = pkg === 'bs4' ? 'bs4' : pkg === 'community' ? 'community' : pkg;
    const result = await runCommand(
      python,
      ['-c', `import ${importName}`],
      configManager.getProjectRoot(),
    );
    rows.push(checkRow('python', `import_${pkg}`, result.code === 0 ? 'pass' : 'fail', result.code === 0 ? `${pkg} importable` : `${pkg} not importable`, { stderr: result.stderr.trim() }));
  }

  return rows;
}

async function parseTomlSections(filePath) {
  try {
    const content = await fs.readFile(filePath, 'utf8');
    const sections = content.match(/\[([^\]]+)\]/g)?.map((s) => s.slice(1, -1)) ?? [];
    return { exists: true, sections, content };
  } catch {
    return { exists: false, sections: [], content: '' };
  }
}

async function checkConfiguration() {
  const rows = [];
  const srcConfig = await parseTomlSections(configManager.resolveProjectPath('src/config.toml'));
  const vizConfig = await parseTomlSections(configManager.resolveProjectPath('viz/config.toml'));

  rows.push(checkRow('config', 'src_config', srcConfig.exists ? 'pass' : 'fail', srcConfig.exists ? 'src/config.toml found' : 'src/config.toml missing'));

  const requiredSrc = ['slicer', 'itext2kg_concepts', 'itext2kg_graph', 'dedup', 'refiner'];
  for (const section of requiredSrc) {
    const found = srcConfig.sections.includes(section);
    rows.push(checkRow('config', `src_${section}`, found ? 'pass' : 'fail', found ? `Section [${section}] present` : `Section [${section}] missing`));
  }

  rows.push(checkRow('config', 'viz_config', vizConfig.exists ? 'pass' : 'fail', vizConfig.exists ? 'viz/config.toml found' : 'viz/config.toml missing'));

  const requiredViz = ['graph2metrics', 'graph2html', 'visualization'];
  for (const section of requiredViz) {
    const found = vizConfig.sections.includes(section);
    rows.push(checkRow('config', `viz_${section}`, found ? 'pass' : 'fail', found ? `Section [${section}] present` : `Section [${section}] missing`));
  }

  if (srcConfig.exists) {
    const providerMatch = srcConfig.content.match(/\[itext2kg_concepts\][\s\S]*?provider\s*=\s*"([^"]+)"/);
    const provider = providerMatch?.[1] ?? 'unknown';
    const valid = ['local_transformers', 'openrouter', 'ollama', 'vllm', 'local'].includes(provider);
    rows.push(checkRow('config', 'provider', valid ? 'pass' : 'warn', `Provider: ${provider}`, { provider }));

    const modelPathMatch = srcConfig.content.match(/local_model_path\s*=\s*"([^"]+)"/);
    if (modelPathMatch) {
      const modelPath = modelPathMatch[1].replace(/NORNIKEL2/gi, 'NORNIKEL3');
      const resolved = path.isAbsolute(modelPath) ? modelPath : configManager.resolveProjectPath(modelPath);
      const hasConfig = await fileExists(path.join(resolved, 'config.json'));
      const hasTokenizer = await fileExists(path.join(resolved, 'tokenizer.json'));
      const hasModel = await fileExists(path.join(resolved, 'model.safetensors'));
      const ok = hasConfig && hasTokenizer && hasModel;
      rows.push(checkRow('config', 'local_llm_model', ok ? 'pass' : 'fail', ok ? 'Local LLM model files present' : 'Local LLM model incomplete', { path: resolved, hasConfig, hasTokenizer, hasModel }));
    }
  }

  return rows;
}

async function checkDirectories() {
  const rows = [];
  for (const dir of REQUIRED_DIRS) {
    const full = configManager.resolveProjectPath(dir);
    let exists = false;
    let writable = false;
    try {
      await fs.access(full);
      exists = true;
      const testFile = path.join(full, '.write_test');
      await fs.writeFile(testFile, 'test', 'utf8');
      await fs.unlink(testFile);
      writable = true;
    } catch {
      try {
        await fs.mkdir(full, { recursive: true });
        exists = true;
        writable = true;
      } catch {
        // remain false
      }
    }
    const status = exists && writable ? 'pass' : exists ? 'warn' : 'fail';
    rows.push(checkRow('directories', dir, status, exists ? (writable ? 'Exists and writable' : 'Exists but not writable') : 'Missing', { exists, writable }));
  }
  return rows;
}

async function checkInputData() {
  const rows = [];
  const rawDir = configManager.resolveProjectPath('data/raw');
  try {
    const entries = await fs.readdir(rawDir);
    const files = [];
    for (const name of entries) {
      const ext = path.extname(name).slice(1).toLowerCase();
      if (['txt', 'md', 'html'].includes(ext)) {
        const stat = await fs.stat(path.join(rawDir, name));
        files.push({ name, size: stat.size });
      }
    }
    if (files.length === 0) {
      rows.push(checkRow('input', 'raw_files', 'warn', 'No input files in data/raw/', { files }));
    } else if (files.some((f) => f.size === 0)) {
      rows.push(checkRow('input', 'raw_files', 'warn', 'Some input files are empty', { files }));
    } else {
      rows.push(checkRow('input', 'raw_files', 'pass', `${files.length} input file(s) found`, { files }));
    }
  } catch {
    rows.push(checkRow('input', 'raw_files', 'fail', 'data/raw/ not accessible', {}));
  }
  return rows;
}

async function checkStaticAssets() {
  const rows = [];
  for (const rel of [...GRAPH2HTML_VENDOR, ...GRAPH2VIEWER_FILES]) {
    const full = configManager.resolveProjectPath(rel);
    const exists = await fileExists(full);
    rows.push(checkRow('assets', rel, exists ? 'pass' : 'fail', exists ? 'Present' : 'Missing', { path: full }));
  }
  return rows;
}

async function checkSchemas() {
  const rows = [];
  const schemasDir = configManager.resolveProjectPath('src/schemas');
  try {
    const files = await fs.readdir(schemasDir);
    const jsonFiles = files.filter((f) => f.endsWith('.json'));
    for (const file of jsonFiles) {
      try {
        JSON.parse(await fs.readFile(path.join(schemasDir, file), 'utf8'));
        rows.push(checkRow('schemas', file, 'pass', 'Parseable JSON schema', {}));
      } catch (error) {
        rows.push(checkRow('schemas', file, 'fail', `Invalid JSON: ${error.message}`, {}));
      }
    }
  } catch {
    rows.push(checkRow('schemas', 'schemas_dir', 'fail', 'src/schemas/ not accessible', {}));
  }
  return rows;
}

async function checkOfflineModels() {
  const rows = [];
  try {
    const runtimeConfig = await fs.readFile(path.join(configManager.getWebRoot(), 'runtime', 'config.toml'), 'utf8');
    const embeddingMatch = runtimeConfig.match(/\[dedup\][\s\S]*?embedding_model\s*=\s*"([^"]+)"/);
    const embeddingPath = embeddingMatch?.[1]?.replace(/NORNIKEL2/gi, 'NORNIKEL3') ?? '';
    if (embeddingPath) {
      const resolved = path.isAbsolute(embeddingPath) ? embeddingPath : configManager.resolveProjectPath(embeddingPath);
      const ok = await fileExists(path.join(resolved, 'config.json')) && await fileExists(path.join(resolved, 'model.safetensors'));
      rows.push(checkRow('models', 'embedding_model', ok ? 'pass' : 'fail', ok ? 'Embedding model available locally' : 'Embedding model not found locally', { path: resolved }));
    }

    const python = configManager.getPythonExecutable();
    const offlineTest = await runCommand(
      python,
      ['-c', 'import os; os.environ["HF_HUB_OFFLINE"]="1"; import sentence_transformers; print("ok")'],
      configManager.getProjectRoot(),
    );
    rows.push(checkRow('models', 'hf_offline', offlineTest.code === 0 ? 'pass' : 'warn', offlineTest.code === 0 ? 'HF_HUB_OFFLINE=1 compatible' : 'HF offline mode may fail', { stderr: offlineTest.stderr.trim() }));
  } catch (error) {
    rows.push(checkRow('models', 'offline_check', 'fail', error.message, {}));
  }
  return rows;
}

export async function runFullSystemAudit() {
  const categories = await Promise.all([
    checkPythonEnvironment(),
    checkConfiguration(),
    checkDirectories(),
    checkInputData(),
    checkStaticAssets(),
    checkSchemas(),
    checkOfflineModels(),
  ]);

  const rows = categories.flat();
  const report = {
    generatedAt: new Date().toISOString(),
    rows,
    summary: {
      pass: rows.filter((r) => r.status === 'pass').length,
      warn: rows.filter((r) => r.status === 'warn').length,
      fail: rows.filter((r) => r.status === 'fail').length,
      total: rows.length,
    },
  };

  const logsDir = configManager.resolveProjectPath('viz/logs');
  await fs.mkdir(logsDir, { recursive: true });
  const timestamp = report.generatedAt.replace(/[:.]/g, '-');
  const logPath = path.join(logsDir, `webapp_audit_${timestamp}.log`);
  const logLines = rows.map((r) => `[${report.generatedAt}] ${r.status.toUpperCase()} | ${r.category}/${r.name}: ${r.message}`);
  await fs.writeFile(logPath, logLines.join('\n'), 'utf8');
  report.logPath = logPath;

  return report;
}
