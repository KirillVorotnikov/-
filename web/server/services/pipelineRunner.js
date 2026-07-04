import fs from 'node:fs/promises';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { configManager } from './configManager.js';
import { loadGraphBundle } from './graphLoader.js';

const STAGE_LABELS = {
  slicer: 'Text slicing',
  concepts: 'Concept extraction',
  graph: 'Graph construction',
  dedup: 'Semantic deduplication',
  refiner: 'Long-range refinement',
  metrics: 'Metrics computation',
  fix: 'Graph enrichment',         // ← НОВОЕ
  split: 'Cluster splitting',       // ← НОВОЕ
  graph2html: 'HTML graph generation',
  graph2viewer: 'Viewer HTML production',
};

const STAGE_MODULES = {
  slicer: 'slicer',
  concepts: 'concepts',
  graph: 'graph',
  dedup: 'dedup',
  refiner: 'refiner',
  metrics: 'metrics',
  fix: 'fix',                       // ← НОВОЕ
  split: 'split',                   // ← НОВОЕ
  graph2html: 'graph2html',
  graph2viewer: 'graph2viewer',
};

/**
 * Executes Python pipeline stages via web/run_with_config.py.
 */
export class PipelineRunner {
  constructor(onLog) {
    this.onLog = onLog ?? (() => {});
    this.activeProcess = null;
    this.cancelRequested = false;
    this.incrementalMode = false;
  }

  async ensureDirectories() {
    const dirs = ['data/raw', 'data/staging', 'data/out', 'logs', 'viz/data/in', 'viz/data/out'];
    for (const dir of dirs) {
      await fs.mkdir(configManager.resolveProjectPath(dir), { recursive: true });
    }
  }

  async listRawFiles() {
    const rawDir = configManager.resolveProjectPath('data/raw');
    try {
      const entries = await fs.readdir(rawDir, { withFileTypes: true });
      const allowed = new Set(['txt', 'md', 'html']);
      const files = [];
      for (const entry of entries) {
        if (!entry.isFile()) continue;
        const ext = path.extname(entry.name).slice(1).toLowerCase();
        if (allowed.has(ext)) {
          const fullPath = path.join(rawDir, entry.name);
          const stat = await fs.stat(fullPath);
          files.push({
            name: entry.name,
            size: stat.size,
            modifiedAt: stat.mtime.toISOString(),
          });
        }
      }
      return files.sort((a, b) => a.name.localeCompare(b.name));
    } catch {
      return [];
    }
  }

  requestCancel() {
    this.cancelRequested = true;
    if (this.activeProcess) {
      this.activeProcess.kill('SIGTERM');
    }
  }

runStage(stage) {
  return new Promise((resolve, reject) => {
    const python = configManager.getPythonExecutable();
    const launcher = path.join(configManager.getWebRoot(), 'run_with_config.py');
    const moduleStage = STAGE_MODULES[stage];
    
    if (!moduleStage) {
      reject(new Error(`Unknown pipeline stage: ${stage}`));
      return;
    }
    
    this.cancelRequested = false;
    
    // === INCREMENTAL FIX: Pass --incremental flag ===
    const args = [launcher, moduleStage];
    if (this.incrementalMode && (stage === 'slicer' || stage === 'graph')) {
      args.push('--incremental');
      this.onLog({
        level: 'info',
        stage,
        message: `Running ${stage} in incremental mode`,
      });
    }
    
    this.onLog({
      level: 'info',
      stage,
      message: `Starting ${STAGE_LABELS[stage] ?? stage}`,
    });
    
    const child = spawn(python, args, {  // ← используем args вместо [launcher, moduleStage]
      cwd: configManager.getProjectRoot(),
      env: {
        ...process.env,
        PYTHONIOENCODING: 'utf-8',
        PYTHONUTF8: '1',
      },
      shell: false,
    });

      this.activeProcess = child;
      let stdout = '';
      let stderr = '';

      child.stdout.on('data', (chunk) => {
        const text = chunk.toString('utf8');
        stdout += text;
        for (const line of text.split(/\r?\n/)) {
          if (line.trim()) {
            this.onLog({ level: 'info', stage, message: line.trim() });
          }
        }
      });

      child.stderr.on('data', (chunk) => {
        const text = chunk.toString('utf8');
        stderr += text;
        for (const line of text.split(/\r?\n/)) {
          if (line.trim()) {
            this.onLog({ level: 'warn', stage, message: line.trim() });
          }
        }
      });

      child.on('error', (error) => {
        this.activeProcess = null;
        reject(error);
      });

      child.on('close', (code) => {
        this.activeProcess = null;
        if (this.cancelRequested) {
          reject(new Error('Pipeline cancelled by user'));
          return;
        }
        if (code === 0) {
          resolve({ stage, stdout, stderr });
        } else {
          reject(new Error(`${STAGE_LABELS[stage] ?? stage} failed with exit code ${code}`));
        }
      });
    });
  }

  async copyArtifactsForViz() {
  const outDir = configManager.resolveProjectPath('data/out');
  const vizInDir = configManager.resolveProjectPath('viz/data/in');
  await fs.mkdir(vizInDir, { recursive: true });

  const graphCandidates = [
    'LearningChunkGraph_longrange.json',
    'LearningChunkGraph_dedup.json',
    'LearningChunkGraph_raw.json',
  ];

  let graphSource = null;
  for (const candidate of graphCandidates) {
    const candidatePath = path.join(outDir, candidate);
    try {
      await fs.access(candidatePath);
      graphSource = candidatePath;
      break;
    } catch {
      // continue
    }
  }

  const conceptSource = path.join(outDir, 'ConceptDictionary.json');

  // === МЕРЖИНГ ВМЕСТО ПЕРЕЗАПИСИ ===

  // 1. Объединяем граф
  if (graphSource) {
    const vizGraphPath = path.join(vizInDir, 'LearningChunkGraph.json');
    await this._mergeGraphFiles(graphSource, vizGraphPath);
    this.onLog({
      level: 'info',
      message: `Graph merged: ${path.basename(graphSource)} → viz/data/in/LearningChunkGraph.json`,
    });
  }

  // 2. Объединяем словарь концептов
  try {
    await fs.access(conceptSource);
    const vizConceptsPath = path.join(vizInDir, 'ConceptDictionary.json');
    await this._mergeConceptFiles(conceptSource, vizConceptsPath);
    this.onLog({
      level: 'info',
      message: 'Concepts merged → viz/data/in/ConceptDictionary.json',
    });
  } catch {
    this.onLog({
      level: 'warn',
      message: 'ConceptDictionary.json not found in data/out',
    });
  }
}

/**
 * Объединяет новый граф с существующим в viz/data/in/.
 * - Узлы дедуплицируются по id (новые заменяют старые с тем же id)
 * - Рёбра дедуплицируются по (source, target, type)
 * - Метаданные обновляются от нового файла
 */
async _mergeGraphFiles(newGraphPath, targetPath) {
  // Загружаем новый граф
  const newRaw = await fs.readFile(newGraphPath, 'utf-8');
  const newData = JSON.parse(newRaw);
  const newNodes = newData.nodes || [];
  const newEdges = newData.edges || [];
  const newMeta = newData._meta || {};

  // Проверяем, существует ли целевой файл
  let existingNodes = [];
  let existingEdges = [];
  let existingMeta = {};

  try {
    const existingRaw = await fs.readFile(targetPath, 'utf-8');
    const existingData = JSON.parse(existingRaw);
    existingNodes = existingData.nodes || [];
    existingEdges = existingData.edges || [];
    existingMeta = existingData._meta || {};
  } catch {
    // Файл не существует — начинаем с пустого
  }

  // === МЕРЖИНГ УЗЛОВ ===
  // Индекс существующих узлов по id
  const nodeMap = new Map();
  for (const node of existingNodes) {
    nodeMap.set(node.id, node);
  }
  // Новые узлы заменяют/дополняют существующие
  let nodesAdded = 0;
  let nodesUpdated = 0;
  for (const node of newNodes) {
    if (nodeMap.has(node.id)) {
      nodesUpdated++;
    } else {
      nodesAdded++;
    }
    nodeMap.set(node.id, node);
  }
  const mergedNodes = Array.from(nodeMap.values());

  // === МЕРЖИНГ РЁБЕР ===
  // Ключ: "source|target|type"
  const edgeMap = new Map();
  for (const edge of existingEdges) {
    const key = `${edge.source}|${edge.target}|${edge.type}`;
    edgeMap.set(key, edge);
  }
  let edgesAdded = 0;
  let edgesUpdated = 0;
  for (const edge of newEdges) {
    const key = `${edge.source}|${edge.target}|${edge.type}`;
    if (edgeMap.has(key)) {
      edgesUpdated++;
    } else {
      edgesAdded++;
    }
    edgeMap.set(key, edge);
  }
  const mergedEdges = Array.from(edgeMap.values());

  // === МЕРЖИНГ МЕТАДАННЫХ ===
  // Сохраняем историю: какие файлы были добавлены
  const mergeHistory = existingMeta._merge_history || [];
  mergeHistory.push({
    timestamp: new Date().toISOString(),
    source: path.basename(newGraphPath),
    nodes_added: nodesAdded,
    nodes_updated: nodesUpdated,
    edges_added: edgesAdded,
    edges_updated: edgesUpdated,
    total_nodes: mergedNodes.length,
    total_edges: mergedEdges.length,
  });

  const mergedMeta = {
    ...existingMeta,
    ...newMeta,
    _merge_history: mergeHistory,
    _merged_at: new Date().toISOString(),
    _total_sources: mergeHistory.length,
  };

  // === СОХРАНЕНИЕ ===
  const mergedData = {
    _meta: mergedMeta,
    nodes: mergedNodes,
    edges: mergedEdges,
  };

  await fs.writeFile(
    targetPath,
    JSON.stringify(mergedData, null, 2),
    'utf-8'
  );

  this.onLog({
    level: 'info',
    message:
      `Graph merge stats: +${nodesAdded} nodes, ~${nodesUpdated} updated, ` +
      `+${edgesAdded} edges, ~${edgesUpdated} updated → ` +
      `${mergedNodes.length} nodes, ${mergedEdges.length} edges total`,
  });
}

/**
 * Объединяет новый словарь концептов с существующим.
 * Дедуплицирует по concept_id.
 */
async _mergeConceptFiles(newConceptsPath, targetPath) {
  const newRaw = await fs.readFile(newConceptsPath, 'utf-8');
  const newData = JSON.parse(newRaw);
  const newConcepts = newData.concepts || [];
  const newMeta = newData._meta || {};

  let existingConcepts = [];
  let existingMeta = {};

  try {
    const existingRaw = await fs.readFile(targetPath, 'utf-8');
    const existingData = JSON.parse(existingRaw);
    existingConcepts = existingData.concepts || [];
    existingMeta = existingData._meta || {};
  } catch {
    // Файл не существует
  }

  // Мержинг по concept_id
  const conceptMap = new Map();
  for (const concept of existingConcepts) {
    conceptMap.set(concept.concept_id, concept);
  }
  let added = 0;
  let updated = 0;
  for (const concept of newConcepts) {
    if (conceptMap.has(concept.concept_id)) {
      updated++;
    } else {
      added++;
    }
    conceptMap.set(concept.concept_id, concept);
  }
  const mergedConcepts = Array.from(conceptMap.values());

  // Метаданные
  const mergeHistory = existingMeta._merge_history || [];
  mergeHistory.push({
    timestamp: new Date().toISOString(),
    source: path.basename(newConceptsPath),
    concepts_added: added,
    concepts_updated: updated,
    total_concepts: mergedConcepts.length,
  });

  const mergedMeta = {
    ...existingMeta,
    ...newMeta,
    _merge_history: mergeHistory,
    _merged_at: new Date().toISOString(),
  };

  const mergedData = {
    _meta: mergedMeta,
    concepts: mergedConcepts,
  };

  await fs.writeFile(
    targetPath,
    JSON.stringify(mergedData, null, 2),
    'utf-8'
  );

  this.onLog({
    level: 'info',
    message:
      `Concepts merge stats: +${added} new, ~${updated} updated → ` +
      `${mergedConcepts.length} concepts total`,
  });
}

  async clearPipelineArtifacts() {
  const dirs = ['data/staging', 'data/out'];
  const results = [];
  
  for (const rel of dirs) {
    const dir = configManager.resolveProjectPath(rel);
    try {
      // Проверяем существование директории
      try {
        await fs.access(dir);
      } catch {
        results.push(`Directory ${rel} does not exist, skipping`);
        continue;
      }
      
      // Читаем содержимое
      const entries = await fs.readdir(dir);
      let removed = 0;
      let failed = 0;
      
      for (const entry of entries) {
        const fullPath = path.join(dir, entry);
        try {
          const stat = await fs.stat(fullPath);
          if (stat.isDirectory()) {
            await fs.rm(fullPath, { recursive: true, force: true, maxRetries: 3 });
          } else {
            await fs.unlink(fullPath);
          }
          removed++;
        } catch (err) {
          failed++;
          this.onLog({
            level: 'warn',
            message: `Failed to remove ${fullPath}: ${err.message}`,
          });
        }
      }
      
      results.push(`Cleared ${rel}: ${removed} removed, ${failed} failed`);
    } catch (err) {
      results.push(`Error processing ${rel}: ${err.message}`);
    }
  }
  
  // Логируем результат очистки
  for (const result of results) {
    this.onLog({ level: 'info', message: result });
  }
}

async runPipeline(stages, { incremental = false } = {}) {
  await this.ensureDirectories();
  
  // === INCREMENTAL FIX: Store flag for runStage ===
  this.incrementalMode = incremental;
  
  if (!incremental) {
    await this.clearPipelineArtifacts();
  } else {
    this.onLog({ level: 'info', message: 'Incremental mode: preserving existing artifacts' });
  }

    const selectedStages = stages?.length ? stages : configManager.settings.pipeline.stages;
    const results = [];

    for (const stage of selectedStages) {
      if (stage === 'metrics') {
        await this.copyArtifactsForViz();
      }
      const result = await this.runStage(stage);
      results.push(result);
    }

    if (!selectedStages.includes('metrics') && configManager.settings.pipeline.autoRunMetrics) {
      await this.copyArtifactsForViz();
      results.push(await this.runStage('metrics'));
    }

    return results;
  }

  async getGraphBundle() {
    return loadGraphBundle();
  }
}

export { STAGE_LABELS };
