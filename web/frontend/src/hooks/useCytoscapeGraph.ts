import cytoscape from 'cytoscape';
import coseBilkent from 'cytoscape-cose-bilkent';
import { useEffect, useRef, useCallback } from 'react';
import type { GraphBundle, GraphNode } from '../api/client';

cytoscape.use(coseBilkent);

const DEFAULT_COLORS: Record<string, string> = {
  Material: '#c87941',
  Property: '#3d9970',
  SynthesisMethod: '#e67e22',
  CharacterizationMethod: '#4a90d9',
  Mechanism: '#8e7cc3',
  FailureMode: '#e74c3c',
  Condition: '#95a5a6',
  Application: '#1abc9c',
  KPI_Target: '#f1c40f',
  Source: '#bdc3c7',
  Concept: '#2ecc71',
  Chunk: '#3498db',
  Assessment: '#f39c12',
  default: '#7f8c8d',
};

const EDGE_STYLES: Record<string, { color: string; dashed: boolean }> = {
  SYNTHESIZED_BY: { color: '#e67e22', dashed: false },
  CHARACTERIZED_BY: { color: '#4a90d9', dashed: true },
  IMPROVES: { color: '#3d9970', dashed: false },
  DEGRADES: { color: '#e74c3c', dashed: true },
  CAUSES: { color: '#8e7cc3', dashed: false },
  APPLIED_IN: { color: '#1abc9c', dashed: true },
  PREREQUISITE: { color: '#3498db', dashed: true },
  RELATED_TO: { color: '#64748b', dashed: true },
  default: { color: '#64748b', dashed: false },
};

function nodeLabel(node: GraphNode): string {
  return node.name ?? node.text ?? node.id;
}

function buildStyles(colors: Record<string, string>) {
  const nodeTypes = new Set(Object.keys(colors));
  const rules: cytoscape.StylesheetStyle[] = [
    {
      selector: 'node',
      style: {
        label: 'data(label)',
        'text-valign': 'center',
        'text-halign': 'center',
        'font-size': 10,
        color: '#fff',
        'text-outline-width': 1,
        'text-outline-color': '#1a2332',
        width: 'mapData(pagerank, 0, 0.05, 28, 56)',
        height: 'mapData(pagerank, 0, 0.05, 28, 56)',
        'background-color': DEFAULT_COLORS.default,
        shape: 'ellipse',
      },
    },
    {
      selector: 'edge',
      style: {
        width: 'mapData(weight, 0, 1, 1.5, 4)',
        'line-color': '#64748b',
        'target-arrow-color': '#64748b',
        'target-arrow-shape': 'triangle',
        'curve-style': 'bezier',
        opacity: 0.75,
      },
    },
    { selector: '.hidden', style: { display: 'none' } },
    { selector: '.highlighted', style: { 'border-width': 4, 'border-color': '#4a90d9' } },
    { selector: '.dimmed', style: { opacity: 0.12 } },
    { selector: 'node[?isKpi]', style: { 'border-width': 3, 'border-color': '#f1c40f' } },
  ];

  for (const type of nodeTypes) {
    if (type === 'default') continue;
    rules.push({
      selector: `node[type = "${type}"]`,
      style: {
        'background-color': colors[type] ?? DEFAULT_COLORS.default,
      },
    });
  }

  for (const [edgeType, style] of Object.entries(EDGE_STYLES)) {
    if (edgeType === 'default') continue;
    rules.push({
      selector: `edge[edgeType = "${edgeType}"]`,
      style: {
        'line-color': style.color,
        'target-arrow-color': style.color,
        'line-style': style.dashed ? 'dashed' : 'solid',
      },
    });
  }

  return rules;
}

function buildElements(bundle: GraphBundle, lodMax: number) {
  let nodes = bundle.graph.nodes ?? [];
  const edges = bundle.graph.edges ?? [];

  if (nodes.length > lodMax) {
    nodes = [...nodes]
      .sort((a, b) => (b.pagerank ?? 0) - (a.pagerank ?? 0))
      .slice(0, lodMax);
  }
  const nodeIds = new Set(nodes.map((n) => n.id));

  const cyNodes = nodes.map((node) => ({
    data: {
      id: node.id,
      label: nodeLabel(node),
      type: node.type ?? 'Unknown',
      definition: node.definition ?? '',
      pagerank: node.pagerank ?? 0,
      betweenness: node.betweenness_centrality ?? 0,
      eduImportance: node.educational_importance ?? 0,
      clusterId: node.cluster_id ?? -1,
      isKpi: node.metadata?.is_kpi === true,
      raw: node,
    },
  }));

  const cyEdges = edges
    .filter((edge) => {
      const s = edge.source ?? edge.from ?? '';
      const t = edge.target ?? edge.to ?? '';
      return nodeIds.has(s) && nodeIds.has(t);
    })
    .map((edge, index) => {
      const edgeType = edge.type ?? edge.relationship ?? 'default';
      return {
        data: {
          id: edge.id ?? `e_${index}`,
          source: edge.source ?? edge.from,
          target: edge.target ?? edge.to,
          label: edgeType,
          edgeType,
          weight: edge.weight ?? edge.attributes?.confidence_score ?? 0.5,
        },
      };
    });

  return [...cyNodes, ...cyEdges];
}

export function useCytoscapeGraph(
  containerRef: React.RefObject<HTMLDivElement | null>,
  colors: Record<string, string> = DEFAULT_COLORS,
  lodMax = 2000,
) {
  const cyRef = useRef<cytoscape.Core | null>(null);
  const onSelectRef = useRef<(node: GraphNode | null) => void>(() => {});

  const destroy = useCallback(() => {
    if (cyRef.current) {
      cyRef.current.destroy();
      cyRef.current = null;
    }
  }, []);

  const initGraph = useCallback(
    (bundle: GraphBundle) => {
      if (!containerRef.current) return false;

      destroy();
      const elements = buildElements(bundle, lodMax);
      if (elements.length === 0) return false;

      cyRef.current = cytoscape({
        container: containerRef.current,
        elements,
        style: buildStyles(colors),
        layout: { name: 'cose-bilkent', animate: false, randomize: true, nodeRepulsion: 8000 },
        wheelSensitivity: 0.2,
        minZoom: 0.08,
        maxZoom: 4,
      });

      cyRef.current.on('tap', 'node', (evt) => {
        onSelectRef.current(evt.target.data('raw') as GraphNode);
      });

      const fitGraph = () => {
        cyRef.current?.resize();
        cyRef.current?.fit(undefined, 80);
      };
      requestAnimationFrame(() => requestAnimationFrame(fitGraph));
      return true;
    },
    [colors, containerRef, destroy, lodMax],
  );

  const resizeAndFit = useCallback(() => {
    if (!cyRef.current) return;
    cyRef.current.resize();
    cyRef.current.fit(undefined, 80);
  }, []);

  useEffect(() => () => destroy(), [destroy]);

  const applyViewMode = useCallback((mode: string) => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().removeClass('hidden dimmed highlighted');
    if (mode === 'all') {
      cy.fit(undefined, 80);
      return;
    }
    const showTypes: Record<string, Set<string>> = {
      materials: new Set(['Material', 'Concept', 'KPI_Target']),
      properties: new Set(['Property', 'KPI_Target']),
      synthesis: new Set(['SynthesisMethod', 'CharacterizationMethod']),
    };
    const allowed = showTypes[mode];
    if (!allowed) return;
    cy.nodes().forEach((node) => {
      if (!allowed.has(node.data('type'))) node.addClass('hidden');
    });
    cy.edges().forEach((edge) => {
      if (edge.source().hasClass('hidden') || edge.target().hasClass('hidden')) {
        edge.addClass('hidden');
      }
    });
    cy.fit(cy.elements(':visible'), 80);
  }, []);

  const applyTypeFilter = useCallback((enabled: Set<string>) => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().removeClass('hidden');
    if (enabled.size === 0) return;
    cy.nodes().forEach((node) => {
      if (!enabled.has(node.data('type'))) node.addClass('hidden');
    });
    cy.edges().forEach((edge) => {
      if (edge.source().hasClass('hidden') || edge.target().hasClass('hidden')) {
        edge.addClass('hidden');
      }
    });
  }, []);

  const search = useCallback((query: string) => {
    const cy = cyRef.current;
    if (!cy) return;
    const q = query.trim().toLowerCase();
    cy.elements().removeClass('highlighted dimmed');
    if (!q) return;
    cy.elements().addClass('dimmed');
    const matches = cy.nodes().filter((n) => String(n.data('label')).toLowerCase().includes(q));
    matches.removeClass('dimmed').addClass('highlighted');
    matches.connectedEdges().removeClass('dimmed');
    if (matches.length) cy.animate({ fit: { eles: matches, padding: 80 } }, { duration: 400 });
  }, []);

  const setOnSelect = useCallback((fn: (node: GraphNode | null) => void) => {
    onSelectRef.current = fn;
  }, []);

  const getStats = useCallback(() => {
    const cy = cyRef.current;
    if (!cy) return { total: 0, visible: 0, edges: 0 };
    return {
      total: cy.nodes().length,
      visible: cy.nodes(':visible').length,
      edges: cy.edges().length,
    };
  }, []);

  return { initGraph, resizeAndFit, applyViewMode, applyTypeFilter, search, setOnSelect, getStats, destroy };
}

export { DEFAULT_COLORS, EDGE_STYLES };
