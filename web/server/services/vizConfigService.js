import fs from 'node:fs/promises';
import path from 'node:path';
import { configManager } from './configManager.js';

const DEFAULT_NODE_SHAPES = {
  Material: 'round-rectangle',
  Property: 'diamond',
  SynthesisMethod: 'hexagon',
  CharacterizationMethod: 'triangle',
  Mechanism: 'ellipse',
  FailureMode: 'octagon',
  Condition: 'barrel',
  Application: 'vee',
  KPI_Target: 'star',
  Source: 'tag',
  Concept: 'ellipse',
  Chunk: 'rectangle',
  Assessment: 'round-rectangle',
};

const DEFAULT_NODE_COLORS = {
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
};

function parseSimpleTomlSection(content, sectionName) {
  const regex = new RegExp(`\\[${sectionName}\\]([\\s\\S]*?)(?=\\n\\[|$)`);
  const match = content.match(regex);
  if (!match) return {};
  const result = {};
  for (const line of match[1].split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const kv = trimmed.match(/^(\w+)\s*=\s*"([^"]*)"/);
    if (kv) {
      result[kv[1]] = kv[2];
    }
  }
  return result;
}

function parseNodeShapes(content) {
  const regex = /\[node_shapes\]([\s\S]*?)(?=\n\[|$)/;
  const match = content.match(regex);
  if (!match) return { ...DEFAULT_NODE_SHAPES };
  const shapes = { ...DEFAULT_NODE_SHAPES };
  for (const line of match[1].split('\n')) {
    const kv = line.trim().match(/^(\w+)\s*=\s*"([^"]*)"/);
    if (kv) shapes[kv[1]] = kv[2];
  }
  return shapes;
}

export async function loadVizConfig() {
  const vizConfigPath = configManager.resolveProjectPath('viz/config.toml');
  let content = '';
  try {
    content = await fs.readFile(vizConfigPath, 'utf8');
  } catch {
    return {
      nodeShapes: DEFAULT_NODE_SHAPES,
      nodeColors: DEFAULT_NODE_COLORS,
      visualization: {},
    };
  }

  return {
    nodeShapes: parseNodeShapes(content),
    nodeColors: DEFAULT_NODE_COLORS,
    visualization: parseSimpleTomlSection(content, 'visualization'),
    graph2html: parseSimpleTomlSection(content, 'graph2html'),
  };
}

export { DEFAULT_NODE_SHAPES, DEFAULT_NODE_COLORS };
