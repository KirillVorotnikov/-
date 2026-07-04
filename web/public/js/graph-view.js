/**
 * Cytoscape graph visualization for materials science knowledge graphs.
 */

const NODE_COLORS = {
  Material: '#c87941',
  Property: '#3d9970',
  SynthesisMethod: '#e67e22',
  CharacterizationMethod: '#4a90d9',
  Mechanism: '#8e7cc3',
  FailureMode: '#e74c3c',
  Condition: '#95a5a6',
  Application: '#1abc9c',
  Concept: '#2ecc71',
  Chunk: '#3498db',
  Assessment: '#f39c12',
  default: '#7f8c8d',
};

const NODE_SHAPES = {
  Material: 'round-rectangle',
  Property: 'diamond',
  SynthesisMethod: 'hexagon',
  CharacterizationMethod: 'triangle',
  Mechanism: 'ellipse',
  FailureMode: 'octagon',
  Chunk: 'rectangle',
  Concept: 'ellipse',
  default: 'ellipse',
};

export class GraphView {
  constructor(container, onNodeSelect) {
    this.container = container;
    this.onNodeSelect = onNodeSelect ?? (() => {});
    this.cy = null;
    this.graphData = null;
    this.concepts = null;
    this.conceptById = new Map();
    this.activeFilters = new Set();
  }

  async load(apiClient) {
    const bundle = await apiClient.getGraph();
    this.graphData = bundle.graph;
    this.concepts = bundle.concepts;
    this.conceptById = new Map((this.concepts?.concepts ?? []).map((c) => [c.concept_id, c]));
    this.render(this.graphData);
    return bundle;
  }

  render(graph) {
    if (this.cy) {
      this.cy.destroy();
    }

    if (typeof cytoscape !== 'undefined' && typeof cytoscapeCoseBilkent !== 'undefined') {
      cytoscape.use(cytoscapeCoseBilkent);
    }

    const elements = this.buildElements(graph);
    const styles = this.buildStyles();

    this.cy = cytoscape({
      container: this.container,
      elements,
      style: styles,
      layout: { name: 'cose-bilkent', animate: true, randomize: true, nodeRepulsion: 8000 },
      wheelSensitivity: 0.2,
      minZoom: 0.08,
      maxZoom: 4,
    });

    this.cy.on('tap', 'node', (event) => {
      this.onNodeSelect(event.target.data());
    });

    this.cy.on('mouseover', 'node', (event) => {
      this.onNodeSelect(event.target.data(), { hover: true });
    });

    return this.cy;
  }

  buildElements(graph) {
    const nodes = (graph.nodes ?? []).map((node) => ({
      data: {
        id: node.id,
        label: node.name ?? node.text ?? node.id,
        type: node.type ?? 'Unknown',
        definition: node.definition ?? '',
        pagerank: node.pagerank ?? 0,
        betweenness: node.betweenness_centrality ?? 0,
        eduImportance: node.educational_importance ?? 0,
        clusterId: node.cluster_id ?? -1,
        isKpi: node.metadata?.is_kpi ?? false,
        raw: node,
      },
    }));

    const edges = (graph.edges ?? []).map((edge, index) => ({
      data: {
        id: edge.id ?? `e_${index}`,
        source: edge.source ?? edge.from,
        target: edge.target ?? edge.to,
        label: edge.type ?? edge.relationship ?? '',
        weight: edge.weight ?? edge.attributes?.confidence_score ?? 0.5,
      },
    }));

    return [...nodes, ...edges];
  }

  buildStyles() {
    return [
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
          width: 'mapData(pagerank, 0, 0.05, 24, 56)',
          height: 'mapData(pagerank, 0, 0.05, 24, 56)',
          'background-color': (ele) => NODE_COLORS[ele.data('type')] ?? NODE_COLORS.default,
          shape: (ele) => NODE_SHAPES[ele.data('type')] ?? NODE_SHAPES.default,
          'border-width': (ele) => (ele.data('isKpi') ? 3 : 1),
          'border-color': '#f1c40f',
        },
      },
      {
        selector: 'edge',
        style: {
          width: 'mapData(weight, 0, 1, 1, 4)',
          'line-color': '#64748b',
          'target-arrow-color': '#64748b',
          'target-arrow-shape': 'triangle',
          'curve-style': 'bezier',
          opacity: 0.65,
          label: 'data(label)',
          'font-size': 8,
          color: '#94a3b8',
          'text-rotation': 'autorotate',
        },
      },
      {
        selector: '.highlighted',
        style: {
          'border-width': 4,
          'border-color': '#4a90d9',
          'z-index': 999,
        },
      },
      {
        selector: '.dimmed',
        style: { opacity: 0.15 },
      },
      {
        selector: '.hidden',
        style: { display: 'none' },
      },
    ];
  }

  applyViewMode(mode) {
    if (!this.cy) return;
    this.cy.elements().removeClass('hidden dimmed highlighted');

    if (mode === 'all') return;

    const showTypes = {
      materials: new Set(['Material', 'Concept']),
      properties: new Set(['Property']),
      synthesis: new Set(['SynthesisMethod', 'CharacterizationMethod']),
    };

    if (mode === 'clusters') {
      this.cy.nodes().forEach((node) => {
        const cluster = node.data('clusterId');
        node.style('background-color', this.clusterColor(cluster));
      });
      return;
    }

    if (mode === 'paths') {
      this.cy.nodes().forEach((node) => {
        const depth = node.data('raw')?.prerequisite_depth ?? 0;
        node.style('background-color', this.depthColor(depth));
      });
      return;
    }

    const allowed = showTypes[mode];
    if (!allowed) return;

    this.cy.nodes().forEach((node) => {
      if (!allowed.has(node.data('type'))) {
        node.addClass('hidden');
      }
    });
    this.cy.edges().forEach((edge) => {
      if (edge.source().hasClass('hidden') || edge.target().hasClass('hidden')) {
        edge.addClass('hidden');
      }
    });
  }

  applyTypeFilters(enabledTypes) {
    if (!this.cy) return;
    this.cy.elements().removeClass('hidden');
    if (enabledTypes.size === 0) return;

    this.cy.nodes().forEach((node) => {
      if (!enabledTypes.has(node.data('type'))) {
        node.addClass('hidden');
      }
    });
    this.cy.edges().forEach((edge) => {
      if (edge.source().hasClass('hidden') || edge.target().hasClass('hidden')) {
        edge.addClass('hidden');
      }
    });
  }

  search(query) {
    if (!this.cy) return;
    const normalized = query.trim().toLowerCase();
    this.cy.elements().removeClass('highlighted dimmed');

    if (!normalized) return;

    this.cy.elements().addClass('dimmed');
    const matches = this.cy.nodes().filter((node) =>
      node.data('label').toLowerCase().includes(normalized),
    );
    matches.removeClass('dimmed').addClass('highlighted');
    matches.connectedEdges().removeClass('dimmed');

    if (matches.length > 0) {
      this.cy.animate({ fit: { eles: matches, padding: 80 } }, { duration: 400 });
    }
  }

  focusNode(nodeId) {
    if (!this.cy) return;
    const node = this.cy.getElementById(nodeId);
    if (!node.length) return;
    this.cy.elements().removeClass('highlighted dimmed');
    node.removeClass('dimmed').addClass('highlighted');
    node.connectedEdges().removeClass('dimmed');
    node.neighborhood().removeClass('dimmed');
    this.cy.animate({ center: { eles: node }, zoom: 1.5 }, { duration: 350 });
    this.onNodeSelect(node.data());
  }

  getStats() {
    if (!this.cy) return { nodes: 0, edges: 0, visible: 0 };
    const visibleNodes = this.cy.nodes(':visible').length;
    return {
      nodes: this.cy.nodes().length,
      edges: this.cy.edges().length,
      visible: visibleNodes,
    };
  }

  getNodeTypes() {
    const types = new Set();
    for (const node of this.graphData?.nodes ?? []) {
      types.add(node.type ?? 'Unknown');
    }
    return [...types].sort();
  }

  clusterColor(clusterId) {
    const palette = ['#c87941', '#3d9970', '#4a90d9', '#e67e22', '#8e7cc3', '#1abc9c'];
    if (clusterId < 0) return NODE_COLORS.default;
    return palette[clusterId % palette.length];
  }

  depthColor(depth) {
    const palette = ['#3d9970', '#4a90d9', '#e67e22', '#e74c3c', '#8e7cc3'];
    return palette[Math.min(depth, palette.length - 1)];
  }
}

export { NODE_COLORS };
