import fs from 'node:fs/promises';
import path from 'node:path';
import { configManager } from './configManager.js';

async function fileExists(targetPath) {
  try {
    await fs.access(targetPath);
    return true;
  } catch {
    return false;
  }
}

function countNodeTypes(nodes) {
  const counts = {};
  for (const node of nodes ?? []) {
    const type = node.type ?? 'Unknown';
    counts[type] = (counts[type] ?? 0) + 1;
  }
  return counts;
}

function buildLoadStatus({ graphPath, conceptsPath, source, graph, concepts, warnings }) {
  const nodes = graph?.nodes ?? [];
  const edges = graph?.edges ?? [];
  return {
    graphPath,
    conceptsPath,
    source,
    nodeCount: nodes.length,
    edgeCount: edges.length,
    nodeTypes: countNodeTypes(nodes),
    hasChunkNodes: nodes.some((n) => n.type === 'Chunk'),
    hasAssessmentNodes: nodes.some((n) => n.type === 'Assessment'),
    warnings,
  };
}

/**
 * Load graph + concepts with explicit source tracking and atomic wow pair loading.
 */
export async function loadGraphBundle() {
  const wowGraph = configManager.resolveProjectPath('viz/data/out/LearningChunkGraph_wow.json');
  const wowConcepts = configManager.resolveProjectPath('viz/data/out/ConceptDictionary_wow.json');
  const fallbackGraph = configManager.resolveProjectPath('viz/data/in/LearningChunkGraph.json');
  const fallbackConcepts = configManager.resolveProjectPath('viz/data/in/ConceptDictionary.json');
  const testGraph = configManager.resolveProjectPath('viz/data/test/tiny_html_data.json');
  const testConcepts = configManager.resolveProjectPath('viz/data/test/tiny_html_concepts.json');

  const warnings = [];
  const wowGraphExists = await fileExists(wowGraph);
  const wowConceptsExists = await fileExists(wowConcepts);

  if (wowGraphExists && wowConceptsExists) {
    const [graphRaw, conceptsRaw] = await Promise.all([
      fs.readFile(wowGraph, 'utf8'),
      fs.readFile(wowConcepts, 'utf8'),
    ]);
    const graph = JSON.parse(graphRaw);
    const concepts = JSON.parse(conceptsRaw);
    if ((graph.nodes ?? []).length === 0) {
      warnings.push('empty_wow_graph');
    }
    const missingMetrics = (graph.nodes ?? []).some(
      (n) => n.pagerank === undefined && n.cluster_id === undefined,
    );
    if (missingMetrics) {
      warnings.push('missing_metrics');
    }
    return {
      graph,
      concepts,
      loadStatus: buildLoadStatus({
        graphPath: wowGraph,
        conceptsPath: wowConcepts,
        source: 'wow',
        graph,
        concepts,
        warnings,
      }),
    };
  }

  if (wowGraphExists && !wowConceptsExists) {
    warnings.push('wow_concepts_missing');
  }
  if (!wowGraphExists && wowConceptsExists) {
    warnings.push('wow_graph_missing');
  }
  if (!wowGraphExists) {
    warnings.push('missing_wow_files');
  }

  const inGraphExists = await fileExists(fallbackGraph);
  const inConceptsExists = await fileExists(fallbackConcepts);
  if (inGraphExists && inConceptsExists) {
    warnings.push('fallback_to_in_data');
    const [graphRaw, conceptsRaw] = await Promise.all([
      fs.readFile(fallbackGraph, 'utf8'),
      fs.readFile(fallbackConcepts, 'utf8'),
    ]);
    const graph = JSON.parse(graphRaw);
    const concepts = JSON.parse(conceptsRaw);
    return {
      graph,
      concepts,
      loadStatus: buildLoadStatus({
        graphPath: fallbackGraph,
        conceptsPath: fallbackConcepts,
        source: 'in',
        graph,
        concepts,
        warnings,
      }),
    };
  }

  warnings.push('fallback_to_test_data');
  const [graphRaw, conceptsRaw] = await Promise.all([
    fs.readFile(testGraph, 'utf8'),
    fs.readFile(testConcepts, 'utf8'),
  ]);
  const graph = JSON.parse(graphRaw);
  const concepts = JSON.parse(conceptsRaw);
  return {
    graph,
    concepts,
    loadStatus: buildLoadStatus({
      graphPath: testGraph,
      conceptsPath: testConcepts,
      source: 'test',
      graph,
      concepts,
      warnings,
    }),
  };
}

export { countNodeTypes, fileExists };
