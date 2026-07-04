import { GraphView } from './graph-view.js';
import { CommandPalette } from './command-palette.js';

const STAGES = [
  { id: 'slicer', label: 'Slice' },
  { id: 'concepts', label: 'Concepts' },
  { id: 'graph', label: 'Graph' },
  { id: 'dedup', label: 'Dedup' },
  { id: 'refiner', label: 'Refiner' },
  { id: 'metrics', label: 'Metrics' },
];

class ApiClient {
  constructor(sessionId) {
    this.sessionId = sessionId;
  }

  headers(extra = {}) {
    return {
      'Content-Type': 'application/json',
      'X-Session-Id': this.sessionId,
      ...extra,
    };
  }

  async request(path, options = {}) {
    const response = await fetch(`/api${path}`, {
      ...options,
      headers: this.headers(options.headers),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.error ?? `Request failed: ${response.status}`);
    }
    if (response.headers.get('Content-Type')?.includes('text/markdown')) {
      return response.text();
    }
    return response.json();
  }

  getConfigStatus() {
    return this.request('/config/status');
  }

  setMode(mode) {
    return this.request('/config/mode', { method: 'POST', body: JSON.stringify({ mode }) });
  }

  listFiles() {
    return this.request('/files');
  }

  uploadFiles(formData) {
    return fetch('/api/upload', {
      method: 'POST',
      headers: { 'X-Session-Id': this.sessionId },
      body: formData,
    }).then(async (response) => {
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.error ?? 'Upload failed');
      }
      return response.json();
    });
  }

  runPipeline(payload) {
    return this.request('/pipeline/run', { method: 'POST', body: JSON.stringify(payload) });
  }

  listJobs() {
    return this.request('/jobs');
  }

  getGraph() {
    return this.request('/graph');
  }

  generateHypotheses(body) {
    return this.request('/hypotheses/generate', { method: 'POST', body: JSON.stringify(body) });
  }

  exportHypotheses(body) {
    return fetch('/api/hypotheses/export', {
      method: 'POST',
      headers: this.headers(),
      body: JSON.stringify({ ...body, format: 'markdown' }),
    }).then((response) => {
      if (!response.ok) throw new Error('Export failed');
      return response.blob();
    });
  }

  getAudit(limit = 100) {
    return this.request(`/audit?limit=${limit}`);
  }

  pauseJob(jobId) {
    return this.request(`/jobs/${jobId}/pause`, { method: 'POST' });
  }

  cancelJob(jobId) {
    return this.request(`/jobs/${jobId}/cancel`, { method: 'POST' });
  }
}

const state = {
  sessionId: localStorage.getItem('k2-18-session-id'),
  activeJobId: null,
  eventSource: null,
  graphView: null,
  api: null,
};

function showToast(message) {
  const toast = document.getElementById('toast');
  toast.textContent = message;
  toast.classList.remove('hidden');
  setTimeout(() => toast.classList.add('hidden'), 3200);
}

function showView(viewName) {
  document.querySelectorAll('.view').forEach((view) => view.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach((btn) => btn.classList.remove('active'));
  document.getElementById(`view-${viewName}`)?.classList.add('active');
  document.querySelector(`.nav-item[data-view="${viewName}"]`)?.classList.add('active');
  if (viewName === 'graph' && state.graphView && !state.graphView.cy) {
    state.graphView.load(state.api).catch((error) => showToast(error.message));
  }
}

async function ensureSession() {
  if (state.sessionId) {
    const existing = await fetch(`/api/sessions/${state.sessionId}`).then((r) => (r.ok ? r.json() : null));
    if (existing?.session) return existing.session.id;
  }
  const created = await fetch('/api/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ state: { theme: document.documentElement.dataset.theme } }),
  }).then((r) => r.json());
  state.sessionId = created.session.id;
  localStorage.setItem('k2-18-session-id', state.sessionId);
  return state.sessionId;
}

function renderStageTrack(activeStage, completedStages = new Set()) {
  const track = document.getElementById('stageTrack');
  track.innerHTML = STAGES.map((stage) => {
    let cls = 'stage-chip';
    if (stage.id === activeStage) cls += ' active';
    if (completedStages.has(stage.id)) cls += ' done';
    return `<div class="${cls}">${stage.label}</div>`;
  }).join('');
}

function renderNodeDetails(data) {
  const panel = document.getElementById('nodeDetails');
  if (!data) {
    panel.classList.add('empty');
    panel.textContent = 'Hover or click a node';
    return;
  }
  panel.classList.remove('empty');
  const raw = data.raw ?? {};
  panel.innerHTML = `
    <strong>${data.label}</strong>
    <div><em>${data.type}</em></div>
    <p>${data.definition || raw.definition || 'No definition available'}</p>
    <div class="metric-row"><span>PageRank</span><span>${(data.pagerank ?? 0).toFixed(4)}</span></div>
    <div class="metric-row"><span>Betweenness</span><span>${(data.betweenness ?? 0).toFixed(4)}</span></div>
    <div class="metric-row"><span>Educational Importance</span><span>${(data.eduImportance ?? 0).toFixed(4)}</span></div>
    <div class="metric-row"><span>Cluster</span><span>${data.clusterId ?? '—'}</span></div>
  `;
}

function renderTypeFilters(types) {
  const container = document.getElementById('typeFilters');
  container.innerHTML = types
    .map(
      (type) =>
        `<label><input type="checkbox" value="${type}" checked> ${type}</label>`,
    )
    .join('');
  container.querySelectorAll('input').forEach((input) => {
    input.addEventListener('change', () => {
      const enabled = new Set(
        [...container.querySelectorAll('input:checked')].map((el) => el.value),
      );
      state.graphView.applyTypeFilters(enabled);
      updateGraphStats();
    });
  });
}

function updateGraphStats() {
  const stats = state.graphView?.getStats() ?? { nodes: 0, edges: 0, visible: 0 };
  document.getElementById('graphStats').textContent =
    `${stats.visible}/${stats.nodes} nodes · ${stats.edges} edges`;
  document.getElementById('kpiNodes').textContent = String(stats.nodes);
  document.getElementById('kpiEdges').textContent = `${stats.edges} edges`;
}

function renderJobs(jobs) {
  const recent = document.getElementById('recentJobs');
  recent.innerHTML = jobs.slice(0, 8).map((job) =>
    `<li><span class="status ${job.status}">${job.status}</span> · ${job.type} · ${new Date(job.createdAt).toLocaleString()}</li>`,
  ).join('') || '<li>No operations yet</li>';

  const active = jobs.find((job) => job.status === 'running');
  document.getElementById('kpiJobStatus').textContent = active ? 'Running' : 'Idle';
  document.getElementById('kpiJobStage').textContent = active?.stage ?? '—';
}

function appendLogLine(message) {
  const consoleEl = document.getElementById('pipelineLog');
  consoleEl.textContent += `${message}\n`;
  consoleEl.scrollTop = consoleEl.scrollHeight;
}

function subscribeToJob(jobId) {
  if (state.eventSource) {
    state.eventSource.close();
  }
  state.activeJobId = jobId;
  state.eventSource = new EventSource(`/api/jobs/${jobId}/stream`);

  state.eventSource.addEventListener('snapshot', (event) => {
    const job = JSON.parse(event.data);
    document.getElementById('jobProgress').style.width = `${job.progress}%`;
    renderStageTrack(job.stage);
    job.logs.forEach((log) => appendLogLine(log.message));
  });

  state.eventSource.addEventListener('update', (event) => {
    const job = JSON.parse(event.data);
    document.getElementById('jobProgress').style.width = `${job.progress}%`;
    renderStageTrack(job.stage);
    document.getElementById('kpiJobStatus').textContent = job.status;
    document.getElementById('kpiJobStage').textContent = job.stage ?? '—';
    if (['completed', 'failed', 'cancelled'].includes(job.status)) {
      refreshDashboard();
      if (job.status === 'completed') {
        state.graphView?.load(state.api).then(() => updateGraphStats()).catch(() => {});
        showToast('Pipeline completed successfully');
      }
    }
  });

  state.eventSource.addEventListener('log', (event) => {
    const log = JSON.parse(event.data);
    appendLogLine(log.message);
  });
}

async function refreshDashboard() {
  const [status, files, jobs, graphBundle] = await Promise.all([
    state.api.getConfigStatus(),
    state.api.listFiles(),
    state.api.listJobs(),
    state.api.getGraph().catch(() => null),
  ]);

  document.getElementById('kpiMode').textContent = status.mode;
  document.getElementById('kpiProvider').textContent = status.activeProvider;
  document.getElementById('kpiFiles').textContent = String(files.files.length);

  document.querySelectorAll('.mode-btn').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.mode === status.mode);
  });

  renderJobs(jobs.jobs);
  if (graphBundle?.graph?.nodes) {
    document.getElementById('kpiNodes').textContent = String(graphBundle.graph.nodes.length);
    document.getElementById('kpiEdges').textContent =
      `${graphBundle.graph.edges?.length ?? 0} edges`;
  }
}

async function runPipeline(incremental = false) {
  document.getElementById('pipelineLog').textContent = '';
  const result = await state.api.runPipeline({ incremental });
  subscribeToJob(result.job.id);
  showView('pipeline');
  showToast('Pipeline started');
}

function renderHypotheses(report) {
  const container = document.getElementById('hypothesisResults');
  if (!report.hypotheses.length) {
    container.innerHTML = '<p>No hypotheses matched the current parameters.</p>';
    return;
  }
  container.innerHTML = report.hypotheses.map((item) => `
    <article class="hypothesis-card">
      <span class="confidence-badge">${(item.confidence * 100).toFixed(0)}% confidence</span>
      <h3>${item.title}</h3>
      <p>${item.summary}</p>
      <p><strong>Category:</strong> ${item.category}</p>
      <p><strong>Suggested experiments:</strong></p>
      <ul>${item.suggestedExperiments.map((exp) => `<li>${exp}</li>`).join('')}</ul>
    </article>
  `).join('');
}

async function loadAudit() {
  const data = await state.api.getAudit();
  const body = document.getElementById('auditBody');
  body.innerHTML = data.entries.slice().reverse().map((entry) => `
    <tr>
      <td>${new Date(entry.timestamp).toLocaleString()}</td>
      <td>${entry.event}</td>
      <td>${JSON.stringify({ ...entry, timestamp: undefined, event: undefined })}</td>
    </tr>
  `).join('');
}

async function init() {
  await ensureSession();
  state.api = new ApiClient(state.sessionId);

  state.graphView = new GraphView(
    document.getElementById('cyContainer'),
    (data) => renderNodeDetails(data),
  );

  const palette = new CommandPalette(
    document.getElementById('commandPalette'),
    document.getElementById('commandInput'),
    document.getElementById('commandResults'),
    {
      showView,
      runPipeline: () => runPipeline(false),
      generateHypotheses: async () => {
        showView('hypotheses');
        const form = document.getElementById('hypothesisForm');
        const formData = new FormData(form);
        const body = Object.fromEntries(formData.entries());
        body.minConfidence = Number(body.minConfidence);
        body.maxResults = Number(body.maxResults);
        const report = await state.api.generateHypotheses(body);
        renderHypotheses(report);
      },
      toggleTheme: () => document.getElementById('themeToggle').click(),
      setMode: async (mode) => {
        await state.api.setMode(mode);
        await refreshDashboard();
        showToast(`Switched to ${mode} mode`);
      },
      focusNode: (nodeId) => {
        showView('graph');
        state.graphView.focusNode(nodeId);
      },
    },
  );

  document.querySelectorAll('.nav-item').forEach((btn) => {
    btn.addEventListener('click', () => showView(btn.dataset.view));
  });

  document.getElementById('commandPaletteBtn').addEventListener('click', () => palette.open());
  document.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      palette.open();
    }
  });

  document.getElementById('themeToggle').addEventListener('click', () => {
    const root = document.documentElement;
    const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
    root.dataset.theme = next;
    localStorage.setItem('k2-18-theme', next);
  });

  document.querySelectorAll('.mode-btn').forEach((btn) => {
    btn.addEventListener('click', async () => {
      await state.api.setMode(btn.dataset.mode);
      await refreshDashboard();
      showToast(`Mode: ${btn.dataset.mode}`);
    });
  });

  const dropzone = document.getElementById('dropzone');
  const fileInput = document.getElementById('fileInput');
  dropzone.addEventListener('click', () => fileInput.click());
  dropzone.addEventListener('dragover', (event) => {
    event.preventDefault();
    dropzone.classList.add('dragover');
  });
  dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
  dropzone.addEventListener('drop', (event) => {
    event.preventDefault();
    dropzone.classList.remove('dragover');
    fileInput.files = event.dataTransfer.files;
  });

  document.getElementById('uploadForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const formData = new FormData();
    for (const file of fileInput.files) {
      formData.append('documents', file);
    }
    const integration = document.querySelector('input[name="integration"]:checked')?.value ?? 'new';
    formData.append('mode', integration);
    await state.api.uploadFiles(formData);
    showToast(`Uploaded ${fileInput.files.length} file(s)`);
    fileInput.value = '';
    await refreshDashboard();
  });

  document.getElementById('runPipelineBtn').addEventListener('click', () => runPipeline(false));
  document.getElementById('pauseJobBtn').addEventListener('click', async () => {
    if (!state.activeJobId) return;
    await state.api.pauseJob(state.activeJobId);
    showToast('Pipeline pause requested');
  });
  document.getElementById('cancelJobBtn').addEventListener('click', async () => {
    if (!state.activeJobId) return;
    await state.api.cancelJob(state.activeJobId);
    showToast('Pipeline cancelled');
  });

  document.getElementById('graphViewMode').addEventListener('change', (event) => {
    state.graphView.applyViewMode(event.target.value);
    updateGraphStats();
  });

  document.getElementById('graphSearch').addEventListener('input', (event) => {
    state.graphView.search(event.target.value);
  });

  document.getElementById('generateHypothesesBtn').addEventListener('click', async () => {
    const form = document.getElementById('hypothesisForm');
    const formData = new FormData(form);
    const body = Object.fromEntries(formData.entries());
    body.minConfidence = Number(body.minConfidence);
    body.maxResults = Number(body.maxResults);
    const report = await state.api.generateHypotheses(body);
    renderHypotheses(report);
  });

  document.getElementById('exportHypothesesBtn').addEventListener('click', async () => {
    const form = document.getElementById('hypothesisForm');
    const formData = new FormData(form);
    const body = Object.fromEntries(formData.entries());
    body.minConfidence = Number(body.minConfidence);
    body.maxResults = Number(body.maxResults);
    const blob = await state.api.exportHypotheses(body);
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'k2-18-hypotheses.md';
    anchor.click();
    URL.revokeObjectURL(url);
  });

  document.querySelector('.nav-item[data-view="audit"]').addEventListener('click', loadAudit);

  renderStageTrack(null);

  const savedTheme = localStorage.getItem('k2-18-theme');
  if (savedTheme) {
    document.documentElement.dataset.theme = savedTheme;
  }

  await refreshDashboard();

  try {
    const bundle = await state.graphView.load(state.api);
    renderTypeFilters(state.graphView.getNodeTypes());
    updateGraphStats();
    palette.setSearchItems(
      (bundle.graph.nodes ?? []).slice(0, 200).map((node) => ({
        label: node.name ?? node.text ?? node.id,
        group: node.type ?? 'Node',
        nodeId: node.id,
        action: null,
      })),
    );
  } catch (error) {
    showToast(`Graph preview: ${error.message}`);
  }
}

init().catch((error) => {
  console.error(error);
  showToast(error.message);
});
