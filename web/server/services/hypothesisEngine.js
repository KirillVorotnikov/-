/**
 * KPI hypothesis generator based on graph topology analysis.
 * Identifies underexplored material-property chains and synthesis gaps.
 */

const MATERIAL_TYPES = new Set([
  'Material',
  'Property',
  'SynthesisMethod',
  'CharacterizationMethod',
  'Mechanism',
  'FailureMode',
  'Condition',
  'Application',
  'Concept',
  'Chunk',
]);

const IMPROVEMENT_EDGE_TYPES = new Set(['IMPROVES', 'ELABORATES', 'PREREQUISITE', 'MENTIONS']);
const DEGRADATION_EDGE_TYPES = new Set(['DEGRADES', 'CAUSES', 'HAS_FAILURE_MODE']);

function nodeLabel(node) {
  return node.name ?? node.text ?? node.id;
}

function nodeCategory(node) {
  return node.type ?? 'Unknown';
}

function isKpiProperty(node) {
  return nodeCategory(node) === 'Property' && (node.metadata?.is_kpi === true || /cycle|capacity|conductivity|strength|efficiency/i.test(nodeLabel(node)));
}

function buildAdjacency(graph) {
  const outgoing = new Map();
  const incoming = new Map();
  for (const node of graph.nodes ?? []) {
    outgoing.set(node.id, []);
    incoming.set(node.id, []);
  }
  for (const edge of graph.edges ?? []) {
    const source = edge.source ?? edge.from;
    const target = edge.target ?? edge.to;
    if (!outgoing.has(source)) outgoing.set(source, []);
    if (!incoming.has(target)) incoming.set(target, []);
    outgoing.get(source).push(edge);
    incoming.get(target).push(edge);
  }
  return { outgoing, incoming };
}

function findNodesByType(graph, type) {
  return (graph.nodes ?? []).filter((node) => nodeCategory(node) === type);
}

function computeConfidence(base, modifiers = []) {
  const value = base + modifiers.reduce((sum, item) => sum + item, 0);
  return Math.max(0.05, Math.min(0.98, Number(value.toFixed(2))));
}

function collectEvidence(edges, nodesById, limit = 3) {
  return edges.slice(0, limit).map((edge) => {
    const source = nodesById.get(edge.source ?? edge.from);
    const target = nodesById.get(edge.target ?? edge.to);
    return {
      edgeType: edge.type ?? edge.relationship ?? 'RELATED',
      source: source ? nodeLabel(source) : edge.source,
      target: target ? nodeLabel(target) : edge.target,
      sourceId: edge.source ?? edge.from,
      targetId: edge.target ?? edge.to,
      quote: edge.attributes?.evidence_quote ?? edge.rationale ?? null,
      confidence: edge.attributes?.confidence_score ?? edge.weight ?? null,
      weight: edge.weight ?? edge.attributes?.confidence_score ?? null,
    };
  });
}

function buildSubgraph(graph, nodeIds, nodesById) {
  const idSet = new Set(nodeIds);
  const subNodes = (graph.nodes ?? []).filter((n) => idSet.has(n.id));
  const subEdges = (graph.edges ?? []).filter((e) => {
    const s = e.source ?? e.from;
    const t = e.target ?? e.to;
    return idSet.has(s) && idSet.has(t);
  });
  return { nodes: subNodes, edges: subEdges };
}

function attachSubgraph(hypothesis, graph, nodesById) {
  hypothesis.subgraph = buildSubgraph(graph, hypothesis.relatedNodes ?? [], nodesById);
  hypothesis.relatedConcepts = (hypothesis.relatedNodes ?? [])
    .map((id) => nodesById.get(id))
    .filter(Boolean)
    .map((n) => nodeLabel(n));
  return hypothesis;
}

/**
 * Generate research hypotheses from a knowledge graph.
 */
export function generateHypotheses(graph, options = {}) {
  const {
    minConfidence = 0.4,
    maxResults = 20,
    focusAreas = ['Material', 'Property', 'SynthesisMethod'],
    targetProperty = null,
    constraints = {},
  } = options;

  const nodes = graph.nodes ?? [];
  const nodesById = new Map(nodes.map((node) => [node.id, node]));
  const { outgoing, incoming } = buildAdjacency(graph);
  const hypotheses = [];

  const materials = findNodesByType(graph, 'Material');
  const properties = findNodesByType(graph, 'Property');
  const synthesisMethods = findNodesByType(graph, 'SynthesisMethod');
  const kpiTargets = findNodesByType(graph, 'KPI_Target');
  const kpiProperties = properties.filter(isKpiProperty);
  const allKpiNodes = [...kpiTargets, ...kpiProperties];

  // Gap 1: Materials without synthesis routes
  for (const material of materials) {
    const edges = outgoing.get(material.id) ?? [];
    const hasSynthesis = edges.some((edge) => {
      const target = nodesById.get(edge.target ?? edge.to);
      return target && nodeCategory(target) === 'SynthesisMethod';
    });
    if (!hasSynthesis && focusAreas.includes('SynthesisMethod')) {
      hypotheses.push({
        id: `hyp_synth_gap_${material.id}`,
        title: `Underspecified synthesis route for ${nodeLabel(material)}`,
        category: 'synthesis_gap',
        confidence: computeConfidence(0.62, [-0.05 * synthesisMethods.length]),
        summary: `${nodeLabel(material)} appears in the corpus without a linked synthesis method, limiting reproducibility and scale-up analysis.`,
        reasoning: [
          'No SYNTHESIZED_BY or equivalent edge connects this material to a process node.',
          'Synthesis-structure-property chains require explicit process linkage for hypothesis testing.',
        ],
        evidence: collectEvidence(edges, nodesById),
        suggestedExperiments: [
          `Map candidate synthesis routes for ${nodeLabel(material)} from literature and attach SYNTHESIZED_BY edges.`,
          'Compare microstructure descriptors after competing synthesis pathways.',
        ],
        relatedNodes: [material.id],
      });
    }
  }

  // Gap 2: KPI properties weakly connected to materials
  for (const property of kpiProperties.length ? kpiProperties : properties) {
    if (targetProperty && !nodeLabel(property).toLowerCase().includes(targetProperty.toLowerCase())) {
      continue;
    }
    const inbound = incoming.get(property.id) ?? [];
    const materialLinks = inbound.filter((edge) => {
      const source = nodesById.get(edge.source ?? edge.from);
      return source && ['Material', 'Concept'].includes(nodeCategory(source));
    });

    if (materialLinks.length <= 1 && focusAreas.includes('Property')) {
      hypotheses.push({
        id: `hyp_kpi_sparse_${property.id}`,
        title: `Underexplored KPI linkage for ${nodeLabel(property)}`,
        category: 'kpi_gap',
        confidence: computeConfidence(0.58, [materialLinks.length * 0.04]),
        summary: `The property "${nodeLabel(property)}" has limited material associations, suggesting incomplete structure-property coverage.`,
        reasoning: [
          `Only ${materialLinks.length} material-level inbound relationship(s) detected.`,
          'Sparse KPI connectivity often indicates missing comparative studies across material classes.',
        ],
        evidence: collectEvidence(materialLinks, nodesById),
        suggestedExperiments: [
          `Screen alternative material candidates for ${nodeLabel(property)} optimization.`,
          'Design factorial experiments varying composition and processing conditions.',
        ],
        relatedNodes: [property.id, ...materialLinks.map((edge) => edge.source ?? edge.from)],
      });
    }
  }

  // Gap 3: Missing mitigation for failure modes
  const failureModes = findNodesByType(graph, 'FailureMode');
  for (const failure of failureModes) {
    const inbound = incoming.get(failure.id) ?? [];
    const mitigations = (graph.edges ?? []).filter((edge) => {
      const target = edge.target ?? edge.to;
      const type = edge.type ?? edge.relationship;
      return target === failure.id && (type === 'MITIGATES' || type === 'PREVENTS');
    });
    if (mitigations.length === 0) {
      hypotheses.push({
        id: `hyp_failure_${failure.id}`,
        title: `Unmitigated failure mode: ${nodeLabel(failure)}`,
        category: 'failure_mode',
        confidence: computeConfidence(0.66, [-0.02 * inbound.length]),
        summary: `Failure mode "${nodeLabel(failure)}" is documented without mitigation strategies in the graph.`,
        reasoning: [
          'Reliability-focused KPI improvements require explicit MITIGATES relationships.',
          'Absence of mitigation edges suggests open reliability research opportunity.',
        ],
        evidence: collectEvidence(inbound, nodesById),
        suggestedExperiments: [
          'Identify coating, dopant, or processing interventions linked to this failure mode.',
          'Validate mitigation under accelerated stress conditions.',
        ],
        relatedNodes: [failure.id],
      });
    }
  }

  // Gap 4: High-centrality bridge nodes with low improvement edges
  for (const node of nodes) {
    const betweenness = node.betweenness_centrality ?? 0;
    const pagerank = node.pagerank ?? 0;
    if (betweenness < 0.05 && pagerank < 0.01) continue;

    const outEdges = outgoing.get(node.id) ?? [];
    const improvementCount = outEdges.filter((edge) =>
      IMPROVEMENT_EDGE_TYPES.has(edge.type ?? edge.relationship ?? ''),
    ).length;
    const degradationCount = outEdges.filter((edge) =>
      DEGRADATION_EDGE_TYPES.has(edge.type ?? edge.relationship ?? ''),
    ).length;

    if (improvementCount === 0 && degradationCount > 0) {
      hypotheses.push({
        id: `hyp_bridge_${node.id}`,
        title: `Optimization lever at bridge node ${nodeLabel(node)}`,
        category: 'topology_bridge',
        confidence: computeConfidence(0.55, [betweenness * 0.5 + pagerank * 2]),
        summary: `${nodeLabel(node)} is structurally central but only participates in degrading relationships — a candidate control point for KPI recovery.`,
        reasoning: [
          `Betweenness=${betweenness.toFixed(3)}, PageRank=${pagerank.toFixed(4)}.`,
          'Bridge nodes with degradation-only edges often mediate failure propagation.',
        ],
        evidence: collectEvidence(outEdges, nodesById),
        suggestedExperiments: [
          'Introduce processing or compositional changes targeting this bridge mechanism.',
          'Measure downstream KPI impact after localized intervention.',
        ],
        relatedNodes: [node.id],
      });
    }
  }

  // Gap 5: Long prerequisite chains without synthesis linkage (educational/material pipeline)
  for (const node of nodes) {
    const depth = node.prerequisite_depth ?? 0;
    if (depth < 3) continue;
    const outEdges = outgoing.get(node.id) ?? [];
    const hasPropertyPath = outEdges.some((edge) => {
      const target = nodesById.get(edge.target ?? edge.to);
      return target && ['Property', 'Concept'].includes(nodeCategory(target));
    });
    if (!hasPropertyPath) {
      hypotheses.push({
        id: `hyp_depth_${node.id}`,
        title: `Deep dependency chain without property outcome: ${nodeLabel(node)}`,
        category: 'dependency_chain',
        confidence: computeConfidence(0.5, [depth * 0.03]),
        summary: `A deep prerequisite chain originates at "${nodeLabel(node)}" but lacks explicit property outcome nodes.`,
        reasoning: [
          `Prerequisite depth=${depth}.`,
          'Incomplete synthesis-structure-property chains limit KPI-targeted optimization.',
        ],
        evidence: collectEvidence(outEdges, nodesById),
        suggestedExperiments: [
          'Extend the chain with measurable property nodes and characterization evidence.',
          'Validate intermediate mechanisms experimentally.',
        ],
        relatedNodes: [node.id],
      });
    }
  }

  // Gap 6: Materials with many properties but few KPI_Target connections
  for (const material of materials) {
    const outEdges = outgoing.get(material.id) ?? [];
    const propertyLinks = outEdges.filter((e) => {
      const t = nodesById.get(e.target ?? e.to);
      return t && ['Property', 'KPI_Target'].includes(nodeCategory(t));
    });
    const kpiLinks = propertyLinks.filter((e) => {
      const t = nodesById.get(e.target ?? e.to);
      return t && (nodeCategory(t) === 'KPI_Target' || isKpiProperty(t));
    });
    if (propertyLinks.length >= 2 && kpiLinks.length === 0) {
      hypotheses.push({
        id: `hyp_kpi_target_gap_${material.id}`,
        title: `KPI gap for ${nodeLabel(material)}`,
        category: 'kpi_target_gap',
        confidence: computeConfidence(0.6, [propertyLinks.length * 0.02]),
        summary: `${nodeLabel(material)} has ${propertyLinks.length} property links but no KPI_Target connections.`,
        reasoning: [
          'Structure-property-KPI chains are incomplete for optimization targeting.',
          'Missing KPI_Target edges limit measurable performance hypotheses.',
        ],
        evidence: collectEvidence(propertyLinks, nodesById),
        suggestedExperiments: [
          `Define KPI_Target nodes for key performance metrics of ${nodeLabel(material)}.`,
          'Link synthesis parameters to KPI outcomes through property intermediates.',
        ],
        relatedNodes: [material.id, ...propertyLinks.map((e) => e.target ?? e.to)],
      });
    }
  }

  // Gap 7: Synthesis without property/KPI outcome
  for (const method of synthesisMethods) {
    const inbound = incoming.get(method.id) ?? [];
    const materialsLinked = inbound.filter((e) => {
      const s = nodesById.get(e.source ?? e.from);
      return s && nodeCategory(s) === 'Material';
    });
    for (const edge of materialsLinked) {
      const matId = edge.source ?? edge.from;
      const matOut = outgoing.get(matId) ?? [];
      const hasPropertyKpi = matOut.some((e) => {
        const t = nodesById.get(e.target ?? e.to);
        return t && ['Property', 'KPI_Target'].includes(nodeCategory(t));
      });
      if (!hasPropertyKpi) {
        hypotheses.push({
          id: `hyp_ssp_chain_${method.id}_${matId}`,
          title: `Incomplete synthesis-structure-property chain via ${nodeLabel(method)}`,
          category: 'ssp_chain_gap',
          confidence: computeConfidence(0.57, []),
          summary: `Synthesis method "${nodeLabel(method)}" produces material "${nodeLabel(nodesById.get(matId))}" without linked properties or KPIs.`,
          reasoning: [
            'Synthesis-structure-property chain is broken at the property/KPI layer.',
          ],
          evidence: collectEvidence([edge, ...matOut], nodesById),
          suggestedExperiments: [
            'Characterize key properties after this synthesis route and link to KPI targets.',
          ],
          relatedNodes: [method.id, matId],
        });
      }
    }
  }

  // Gap 8: High bridge_score cross-cluster nodes
  for (const node of nodes) {
    const bridge = node.bridge_score ?? 0;
    if (bridge < 0.5) continue;
    const clusterId = node.cluster_id ?? -1;
    const neighbors = [...(outgoing.get(node.id) ?? []), ...(incoming.get(node.id) ?? [])];
    const crossCluster = neighbors.filter((e) => {
      const otherId = (e.source ?? e.from) === node.id ? (e.target ?? e.to) : (e.source ?? e.from);
      const other = nodesById.get(otherId);
      return other && (other.cluster_id ?? -1) !== clusterId && clusterId >= 0;
    });
    if (crossCluster.length >= 2) {
      hypotheses.push({
        id: `hyp_cross_cluster_${node.id}`,
        title: `Cross-domain bridge: ${nodeLabel(node)}`,
        category: 'cross_cluster',
        confidence: computeConfidence(0.64, [bridge * 0.2]),
        summary: `"${nodeLabel(node)}" bridges ${crossCluster.length} cross-cluster connections (bridge_score=${bridge.toFixed(2)}).`,
        reasoning: [
          'High bridge_score nodes connecting clusters suggest cross-domain research opportunities.',
        ],
        evidence: collectEvidence(crossCluster, nodesById),
        suggestedExperiments: [
          'Explore interdisciplinary optimization leveraging this bridge concept.',
        ],
        relatedNodes: [node.id, ...crossCluster.flatMap((e) => [e.source ?? e.from, e.target ?? e.to])],
      });
    }
  }

  // Gap 9: High educational_importance, low prerequisite_depth entry points
  for (const node of nodes) {
    const importance = node.educational_importance ?? 0;
    const depth = node.prerequisite_depth ?? 0;
    if (importance > 0.05 && depth <= 1) {
      hypotheses.push({
        id: `hyp_entry_${node.id}`,
        title: `Accessible research entry point: ${nodeLabel(node)}`,
        category: 'entry_point',
        confidence: computeConfidence(0.52, [importance * 2]),
        summary: `"${nodeLabel(node)}" has high educational importance (${importance.toFixed(3)}) with low prerequisite depth (${depth}).`,
        reasoning: [
          'Low-barrier, high-impact nodes are promising starting points for new research programs.',
        ],
        evidence: collectEvidence(outgoing.get(node.id) ?? [], nodesById),
        suggestedExperiments: [
          'Design exploratory experiments starting from this concept and measure KPI uplift.',
        ],
        relatedNodes: [node.id],
      });
    }
  }

  const enriched = hypotheses.map((h) => attachSubgraph(h, graph, nodesById));
  const filtered = enriched
    .filter((item) => item.confidence >= minConfidence)
    .sort((a, b) => b.confidence - a.confidence)
    .slice(0, maxResults);

  return {
    generatedAt: new Date().toISOString(),
    totalCandidates: hypotheses.length,
    hypotheses: filtered,
    parameters: { minConfidence, maxResults, focusAreas, targetProperty, constraints },
  };
}

export function exportHypothesesMarkdown(report, labels = {}) {
  const title = labels.title ?? 'K2-18 KPI Hypothesis Report';
  const lines = [
    `# ${title}`,
    '',
    `${labels.generated ?? 'Generated'}: ${report.generatedAt}`,
    '',
  ];

  for (const [index, item] of report.hypotheses.entries()) {
    lines.push(`## ${index + 1}. ${item.title}`);
    lines.push('');
    lines.push(`- **${labels.category ?? 'Category'}:** ${item.category}`);
    lines.push(`- **${labels.confidence ?? 'Confidence'}:** ${item.confidence}`);
    lines.push(`- **${labels.summary ?? 'Summary'}:** ${item.summary}`);
    lines.push('');
    lines.push(`### ${labels.reasoning ?? 'Reasoning'}`);
    for (const reason of item.reasoning) {
      lines.push(`- ${reason}`);
    }
    lines.push('');
    lines.push(`### ${labels.experiments ?? 'Suggested Experiments'}`);
    for (const experiment of item.suggestedExperiments) {
      lines.push(`- ${experiment}`);
    }
    lines.push('');
  }

  return lines.join('\n');
}

export async function exportHypothesesPdf(report, labels = {}) {
  const PDFDocument = (await import('pdfkit')).default;
  return new Promise((resolve, reject) => {
    const doc = new PDFDocument({ margin: 50 });
    const chunks = [];
    doc.on('data', (chunk) => chunks.push(chunk));
    doc.on('end', () => resolve(Buffer.concat(chunks)));
    doc.on('error', reject);

    doc.fontSize(18).text(labels.title ?? 'K2-18 KPI Hypothesis Report');
    doc.moveDown();
    doc.fontSize(10).text(`${labels.generated ?? 'Generated'}: ${report.generatedAt}`);
    doc.moveDown();

    for (const [index, item] of report.hypotheses.entries()) {
      doc.fontSize(14).text(`${index + 1}. ${item.title}`);
      doc.fontSize(10).text(`${labels.confidence ?? 'Confidence'}: ${item.confidence}`);
      doc.text(item.summary);
      doc.moveDown();
    }
    doc.end();
  });
}

export { MATERIAL_TYPES };
