import fs from 'node:fs/promises';
import path from 'node:path';
import Ajv from 'ajv';
import { configManager } from './configManager.js';
import { loadGraphBundle, fileExists, countNodeTypes } from './graphLoader.js';

const REQUIRED_VIEWER_FILES = [
  'viz/templates/viewer/index.html',
  'viz/templates/viewer/viewer_styles.css',
  'viz/static/viewer/viewer_core.js',
  'viz/static/viewer/node_explorer.js',
  'viz/static/viewer/edge_inspector.js',
  'viz/static/viewer/search_filter.js',
  'viz/static/viewer/formatters.js',
  'viz/static/viewer/navigation_history.js',
];

const REQUIRED_VENDOR_FILES = [
  'viz/vendor/marked.min.js',
  'viz/vendor/highlight.min.js',
];

const METRIC_FIELDS = [
  'pagerank',
  'cluster_id',
  'bridge_score',
  'prerequisite_depth',
  'learning_effort',
  'educational_importance',
];

const NORNIKEL_TYPES = new Set([
  'Material', 'Property', 'SynthesisMethod', 'CharacterizationMethod',
  'FailureMode', 'Mechanism', 'Condition', 'Application', 'Source', 'KPI_Target',
]);

function step(id, titleKey, status, messageKey, details = {}) {
  return { id, titleKey, status, messageKey, message: messageKey, details };
}

async function checkFileExistence() {
  const graphPath = configManager.resolveProjectPath('viz/data/out/LearningChunkGraph_wow.json');
  const conceptsPath = configManager.resolveProjectPath('viz/data/out/ConceptDictionary_wow.json');
  const graphOk = await fileExists(graphPath);
  const conceptsOk = await fileExists(conceptsPath);

  if (graphOk && conceptsOk) {
    return step('file_existence', 'diagnostics.steps.file_existence', 'pass', 'diagnostics.messages.files_ok', {
      graphPath, conceptsPath,
    });
  }
  return step('file_existence', 'diagnostics.steps.file_existence', 'fail', 'diagnostics.messages.files_missing', {
    graphExists: graphOk,
    conceptsExists: conceptsOk,
    graphPath,
    conceptsPath,
    suggestRunMetrics: true,
  });
}

async function checkSchemaConformance() {
  const graphPath = configManager.resolveProjectPath('src/schemas/LearningChunkGraph.schema.json');
  const conceptPath = configManager.resolveProjectPath('src/schemas/ConceptDictionary.schema.json');
  const issues = [];

  try {
    const bundle = await loadGraphBundle();
    const graph = bundle.graph;
    const concepts = bundle.concepts;

    if (!Array.isArray(graph.nodes)) issues.push('graph.nodes missing or not array');
    if (!Array.isArray(graph.edges)) issues.push('graph.edges missing or not array');
    if (!concepts.concepts) issues.push('concepts.concepts missing');

    for (const [index, node] of (graph.nodes ?? []).entries()) {
      if (!node.id) issues.push(`node[${index}].id missing`);
      if (!node.type) issues.push(`node[${index}].type missing`);
      if (!node.name && !node.text) issues.push(`node[${index}].name/text missing`);
      for (const field of METRIC_FIELDS) {
        if (bundle.loadStatus.source === 'wow' && node[field] === undefined) {
          issues.push(`node[${node.id ?? index}].${field} missing`);
        }
      }
    }

    for (const [index, edge] of (graph.edges ?? []).entries()) {
      if (!edge.source && !edge.from) issues.push(`edge[${index}].source missing`);
      if (!edge.target && !edge.to) issues.push(`edge[${index}].target missing`);
      if (!edge.type && !edge.relationship) issues.push(`edge[${index}].type missing`);
    }

    if (bundle.loadStatus.source === 'wow' && !concepts._meta?.mention_index) {
      issues.push('concepts._meta.mention_index missing');
    }

    try {
      const ajv = new Ajv({ allErrors: true, strict: false });
      const graphSchema = JSON.parse(await fs.readFile(graphPath, 'utf8'));
      const validateGraph = ajv.compile(graphSchema);
      if (!validateGraph(graph)) {
        issues.push(...(validateGraph.errors ?? []).map((e) => `${e.instancePath} ${e.message}`));
      }
    } catch (schemaError) {
      issues.push(`schema_validation_error: ${schemaError.message}`);
    }

    if (issues.length === 0) {
      return step('schema_conformance', 'diagnostics.steps.schema', 'pass', 'diagnostics.messages.schema_ok', {});
    }
    return step('schema_conformance', 'diagnostics.steps.schema', 'warn', 'diagnostics.messages.schema_issues', {
      issues: issues.slice(0, 50),
      totalIssues: issues.length,
    });
  } catch (error) {
    return step('schema_conformance', 'diagnostics.steps.schema', 'fail', 'diagnostics.messages.schema_fail', {
      error: error.message,
    });
  }
}

async function checkNodeComposition() {
  try {
    const bundle = await loadGraphBundle();
    const nodeTypes = countNodeTypes(bundle.graph.nodes);
    const typeCount = Object.keys(nodeTypes).length;
    const status = typeCount <= 1 ? 'warn' : 'pass';
    const messageKey = typeCount <= 1 ? 'diagnostics.messages.single_type' : 'diagnostics.messages.composition_ok';
    return step('node_composition', 'diagnostics.steps.composition', status, messageKey, { nodeTypes, typeCount });
  } catch (error) {
    return step('node_composition', 'diagnostics.steps.composition', 'fail', 'diagnostics.messages.composition_fail', {
      error: error.message,
    });
  }
}

async function checkEdgeIntegrity() {
  try {
    const bundle = await loadGraphBundle();
    const nodeIds = new Set((bundle.graph.nodes ?? []).map((n) => n.id));
    const dangling = [];
    for (const edge of bundle.graph.edges ?? []) {
      const source = edge.source ?? edge.from;
      const target = edge.target ?? edge.to;
      if (!nodeIds.has(source)) dangling.push({ edge, issue: 'missing_source', id: source });
      if (!nodeIds.has(target)) dangling.push({ edge, issue: 'missing_target', id: target });
    }
    if (dangling.length === 0) {
      return step('edge_integrity', 'diagnostics.steps.edges', 'pass', 'diagnostics.messages.edges_ok', {});
    }
    return step('edge_integrity', 'diagnostics.steps.edges', 'fail', 'diagnostics.messages.dangling_edges', {
      count: dangling.length,
      samples: dangling.slice(0, 10),
    });
  } catch (error) {
    return step('edge_integrity', 'diagnostics.steps.edges', 'fail', 'diagnostics.messages.edges_fail', {
      error: error.message,
    });
  }
}

async function checkMentionIndex() {
  try {
    const bundle = await loadGraphBundle();
    const mentionIndex = bundle.concepts?._meta?.mention_index ?? {};
    const indexKeys = Object.keys(mentionIndex);
    const nodeTypes = countNodeTypes(bundle.graph.nodes);
    const isNornikel = Object.keys(nodeTypes).some((t) => NORNIKEL_TYPES.has(t));

    const conceptIds = new Set((bundle.concepts?.concepts ?? []).map((c) => c.concept_id));
    const graphIds = new Set((bundle.graph.nodes ?? []).map((n) => n.id));
    const matchedConcepts = [...conceptIds].filter((id) => graphIds.has(id));
    const unmatchedConcepts = [...conceptIds].filter((id) => !graphIds.has(id));

    if (isNornikel && indexKeys.length === 0) {
      return step('mention_index', 'diagnostics.steps.mention_index', 'warn', 'diagnostics.messages.mention_index_nornikel', {
        note: 'graph2metrics indexes only type=Concept nodes; NORNIKEL graphs use alternate concept_id check',
        matchedConcepts: matchedConcepts.length,
        unmatchedConcepts: unmatchedConcepts.slice(0, 20),
        totalConcepts: conceptIds.size,
      });
    }
    if (indexKeys.length === 0) {
      return step('mention_index', 'diagnostics.steps.mention_index', 'warn', 'diagnostics.messages.mention_index_empty', {
        matchedConcepts: matchedConcepts.length,
      });
    }
    return step('mention_index', 'diagnostics.steps.mention_index', 'pass', 'diagnostics.messages.mention_index_ok', {
      indexedConcepts: indexKeys.length,
    });
  } catch (error) {
    return step('mention_index', 'diagnostics.steps.mention_index', 'fail', 'diagnostics.messages.mention_index_fail', {
      error: error.message,
    });
  }
}

async function checkStaticAssets() {
  const missing = [];
  for (const rel of [...REQUIRED_VIEWER_FILES, ...REQUIRED_VENDOR_FILES]) {
    const full = configManager.resolveProjectPath(rel);
    if (!(await fileExists(full))) missing.push(rel);
  }
  if (missing.length === 0) {
    return step('static_assets', 'diagnostics.steps.assets', 'pass', 'diagnostics.messages.assets_ok', {});
  }
  return step('static_assets', 'diagnostics.steps.assets', 'fail', 'diagnostics.messages.assets_missing', { missing });
}

async function checkEmbeddingModel() {
  try {
    const runtimeConfig = await fs.readFile(
      path.join(configManager.getWebRoot(), 'runtime', 'config.toml'),
      'utf8',
    );
    const match = runtimeConfig.match(/\[dedup\][\s\S]*?embedding_model\s*=\s*"([^"]+)"/);
    const modelPath = match?.[1] ?? '';
    if (!modelPath) {
      return step('embedding_model', 'diagnostics.steps.embedding', 'fail', 'diagnostics.messages.embedding_not_configured', {});
    }

    const resolved = path.isAbsolute(modelPath)
      ? modelPath
      : configManager.resolveProjectPath(modelPath);

    if (await fileExists(resolved)) {
      const configJson = path.join(resolved, 'config.json');
      const modelFile = path.join(resolved, 'model.safetensors');
      const hasConfig = await fileExists(configJson);
      const hasModel = await fileExists(modelFile);
      if (hasConfig && hasModel) {
        return step('embedding_model', 'diagnostics.steps.embedding', 'pass', 'diagnostics.messages.embedding_ok', {
          path: resolved,
        });
      }
      return step('embedding_model', 'diagnostics.steps.embedding', 'warn', 'diagnostics.messages.embedding_incomplete', {
        path: resolved, hasConfig, hasModel,
      });
    }

    const hfCache = process.env.HF_HOME ?? path.join(process.env.USERPROFILE ?? process.env.HOME ?? '', '.cache', 'huggingface', 'hub');
    return step('embedding_model', 'diagnostics.steps.embedding', 'warn', 'diagnostics.messages.embedding_hf', {
      modelPath,
      hfCache,
      localMissing: true,
    });
  } catch (error) {
    return step('embedding_model', 'diagnostics.steps.embedding', 'fail', 'diagnostics.messages.embedding_fail', {
      error: error.message,
    });
  }
}

async function checkRenderSmoke(graph) {
  try {
    const nodes = graph?.nodes ?? [];
    const edges = graph?.edges ?? [];
    const elementCount = nodes.length + edges.length;
    const cytoscapeOk = nodes.length > 0 && elementCount > 0;
    const status = cytoscapeOk ? 'pass' : 'fail';
    return step('render_smoke', 'diagnostics.steps.render', status, cytoscapeOk ? 'diagnostics.messages.render_ok' : 'diagnostics.messages.render_fail', {
      cytoscapeOk,
      elementCount,
      nodeCount: nodes.length,
      edgeCount: edges.length,
    });
  } catch (error) {
    return step('render_smoke', 'diagnostics.steps.render', 'fail', 'diagnostics.messages.render_fail', {
      error: error.message,
    });
  }
}

export async function runOfflineDiagnostics() {
  const steps = [];
  steps.push(await checkFileExistence());
  steps.push(await checkSchemaConformance());
  steps.push(await checkNodeComposition());
  steps.push(await checkEdgeIntegrity());
  steps.push(await checkMentionIndex());
  steps.push(await checkStaticAssets());
  steps.push(await checkEmbeddingModel());

  let graph = { nodes: [], edges: [] };
  try {
    const bundle = await loadGraphBundle();
    graph = bundle.graph;
  } catch {
    // render test will fail
  }
  steps.push(await checkRenderSmoke(graph));

  const report = {
    generatedAt: new Date().toISOString(),
    mode: configManager.getMode(),
    provider: configManager.getProviderForMode(),
    steps,
    summary: {
      pass: steps.filter((s) => s.status === 'pass').length,
      warn: steps.filter((s) => s.status === 'warn').length,
      fail: steps.filter((s) => s.status === 'fail').length,
    },
  };

  const logsDir = configManager.resolveProjectPath('viz/logs');
  await fs.mkdir(logsDir, { recursive: true });
  const timestamp = report.generatedAt.replace(/[:.]/g, '-');
  const reportPath = path.join(logsDir, `offline_diagnostics_${timestamp}.json`);
  await fs.writeFile(reportPath, JSON.stringify(report, null, 2), 'utf8');
  report.reportPath = reportPath;

  return report;
}

export async function listDiagnosticReports() {
  const logsDir = configManager.resolveProjectPath('viz/logs');
  try {
    const files = await fs.readdir(logsDir);
    return files
      .filter((f) => f.startsWith('offline_diagnostics_') && f.endsWith('.json'))
      .sort()
      .reverse();
  } catch {
    return [];
  }
}

export async function getLatestDiagnosticReport() {
  const files = await listDiagnosticReports();
  if (files.length === 0) return null;
  const content = await fs.readFile(
    path.join(configManager.resolveProjectPath('viz/logs'), files[0]),
    'utf8',
  );
  return JSON.parse(content);
}

export function runRenderTest(graph) {
  const nodes = graph?.nodes ?? [];
  const edges = graph?.edges ?? [];
  return {
    cytoscapeOk: nodes.length > 0,
    elementCount: nodes.length + edges.length,
    nodeCount: nodes.length,
    edgeCount: edges.length,
  };
}
