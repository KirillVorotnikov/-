import { Router } from 'express';
import multer from 'multer';
import path from 'node:path';
import fs from 'node:fs/promises';
import { v4 as uuidv4 } from 'uuid';
import { configManager } from '../services/configManager.js';
import { jobQueue } from '../services/jobQueue.js';
import { feynmanBridge } from '../services/feynmanBridge.js';
import { requireApiKey, rateLimit } from '../middleware/security.js';
import { PipelineRunner } from '../services/pipelineRunner.js';
import { auditLog } from '../services/auditLog.js';
import { sessionManager } from '../services/sessionManager.js';
import { stateStore } from '../services/stateStore.js';
import { loadVizConfig } from '../services/vizConfigService.js';
import {
  runOfflineDiagnostics,
  getLatestDiagnosticReport,
  listDiagnosticReports,
  runRenderTest,
} from '../services/diagnosticsService.js';
import { runFullSystemAudit } from '../services/systemAuditService.js';
import {
  generateHypotheses,
  exportHypothesesMarkdown,
  exportHypothesesPdf,
} from '../services/hypothesisEngine.js';
import { fileExists } from '../services/graphLoader.js';
import {
  sanitizeSlug,
  listResults as listAccelmatResults,
  readResult as readAccelmatResult,
  listGraphs as listAccelmatGraphs,
  getAccelmatDefaults,
} from '../services/accelmatRunner.js';

const router = Router();
const upload = multer({
  storage: multer.diskStorage({
    destination: async (_req, _file, cb) => {
      const rawDir = configManager.resolveProjectPath('data/raw');
      await fs.mkdir(rawDir, { recursive: true });
      cb(null, rawDir);
    },
    filename: (req, file, cb) => {
      // === FIX: Allow Cyrillic and Unicode characters ===
      const safeName = file.originalname
        .replace(/[\\/:*?"<>|\x00-\x1f]/g, '')  // Remove dangerous chars only
        .trim()
        .slice(0, 200);
      
      // Fallback if name became empty
      if (!safeName || safeName.startsWith('.')) {
        const ext = path.extname(file.originalname);
        const timestamp = Date.now();
        cb(null, `uploaded_${timestamp}${ext}`);
      } else {
        cb(null, safeName);
      }
    },
  }),
  limits: { fileSize: 50 * 1024 * 1024 },
  fileFilter: (_req, file, cb) => {
    const ext = path.extname(file.originalname).slice(1).toLowerCase();
    if (['txt', 'md', 'html'].includes(ext)) {
      cb(null, true);
    } else {
      cb(new Error(`Unsupported file type: ${ext}`));
    }
  },
});

function getSessionId(req) {
  return req.headers['x-session-id'] ?? null;
}

// ACCELMAT (multi-minute LLM pipeline) and Feynman (full bash/read/write agent)
// are the highest-impact routes in this app; gate them behind an optional
// shared API key and per-client rate limits. See server/middleware/security.js.
router.use(['/accelmat', '/feynman'], requireApiKey);

const accelmatRunLimiter = rateLimit({
  windowMs: 10 * 60 * 1000,
  max: 5,
  message: 'Too many ACCELMAT runs requested; wait a few minutes before starting another.',
});
const feynmanSessionLimiter = rateLimit({
  windowMs: 10 * 60 * 1000,
  max: 10,
  message: 'Too many Feynman chat sessions started; wait a few minutes before starting another.',
});
const feynmanMessageLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 30,
  message: 'Too many messages sent to Feynman; slow down.',
});

router.get('/health', (_req, res) => {
  res.json({ status: 'ok', service: 'k2-18-web', timestamp: new Date().toISOString() });
});

router.get('/config/status', async (_req, res, next) => {
  try {
    const status = await configManager.getStatus();
    const runtimeConfig = await fs.readFile(
      path.join(configManager.getWebRoot(), 'runtime', 'config.toml'),
      'utf8',
    ).catch(() => '');
    const modelMatch = runtimeConfig.match(/model\s*=\s*"([^"]+)"/);
    const localPathMatch = runtimeConfig.match(/local_model_path\s*=\s*"([^"]+)"/);
    res.json({
      ...status,
      modelName: modelMatch?.[1] ?? null,
      localModelPath: localPathMatch?.[1] ?? null,
      integrationMode: stateStore.get()?.integrationMode ?? 'new',
    });
  } catch (error) {
    next(error);
  }
});

router.get('/viz-config', async (_req, res, next) => {
  try {
    res.json(await loadVizConfig());
  } catch (error) {
    next(error);
  }
});

router.post('/config/mode', async (req, res, next) => {
  try {
    const { mode, force } = req.body ?? {};
    if (!force && stateStore.needsRegenerationWarning(mode)) {
      res.status(409).json({
        error: 'mode_switch_warning',
        message: 'Artifacts were generated under a different provider and may need regeneration',
        lastProvider: stateStore.get().artifactsMetadata?.lastProvider,
      });
      return;
    }
    const settings = await configManager.setMode(mode);
    await stateStore.update({ mode: settings.mode, provider: configManager.getProviderForMode(mode) });
    await auditLog.record('mode_changed', { mode, sessionId: getSessionId(req) });
    res.json({ mode: settings.mode, provider: configManager.getProviderForMode(settings.mode) });
  } catch (error) {
    next(error);
  }
});

router.get('/state', (_req, res) => {
  res.json({ state: stateStore.get() });
});

router.patch('/state', async (req, res, next) => {
  try {
    const state = await stateStore.update(req.body ?? {});
    res.json({ state });
  } catch (error) {
    next(error);
  }
});

router.get('/files', async (_req, res, next) => {
  try {
    const runner = new PipelineRunner();
    const files = await runner.listRawFiles();
    res.json({ files });
  } catch (error) {
    next(error);
  }
});

router.post('/upload', upload.array('documents', 20), async (req, res, next) => {
  try {
    const mode = req.body?.mode ?? 'new';
    const files = (req.files ?? []).map((file) => ({
      name: file.filename,
      size: file.size,
      path: file.path,
    }));
    await stateStore.update({ integrationMode: mode === 'incremental' ? 'incremental' : 'new' });
    await auditLog.record('files_uploaded', {
      count: files.length,
      mode,
      sessionId: getSessionId(req),
    });
    res.json({ uploaded: files, integrationMode: mode });
  } catch (error) {
    next(error);
  }
});

router.post('/pipeline/run', async (req, res, next) => {
  try {
    const { stages, incremental = false } = req.body ?? {};
    const job = jobQueue.createJob({
      type: 'pipeline',
      payload: { stages, incremental },
    });
    await stateStore.update({ activeJobId: job.id });
    await auditLog.record('pipeline_started', {
      jobId: job.id,
      incremental,
      sessionId: getSessionId(req),
    });
    res.status(202).json({ job });
  } catch (error) {
    next(error);
  }
});

router.get('/jobs', (_req, res) => {
  res.json({ jobs: jobQueue.listJobs() });
});

router.get('/jobs/:jobId', (req, res) => {
  const job = jobQueue.getJob(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: 'Job not found' });
    return;
  }
  res.json({ job });
});

router.get('/jobs/:jobId/stream', (req, res) => {
  const job = jobQueue.getJob(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: 'Job not found' });
    return;
  }

  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
  });

  const send = (event, data) => {
    res.write(`event: ${event}\n`);
    res.write(`data: ${JSON.stringify(data)}\n\n`);
  };

  send('snapshot', job);

  const onUpdate = (updated) => {
    if (updated.id !== job.id) return;
    send('update', updated);
    if (['completed', 'failed', 'cancelled'].includes(updated.status)) {
      cleanup();
      res.end();
    }
  };

  const onLog = (updated, entry) => {
    if (updated.id !== job.id) return;
    send('log', entry);
  };

  const cleanup = () => {
    jobQueue.off('job:updated', onUpdate);
    jobQueue.off('job:log', onLog);
  };

  jobQueue.on('job:updated', onUpdate);
  jobQueue.on('job:log', onLog);

  req.on('close', cleanup);
});

router.post('/jobs/:jobId/pause', async (req, res) => {
  const job = jobQueue.pauseJob(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: 'Job not found or not running' });
    return;
  }
  await auditLog.record('job_paused', { jobId: job.id, sessionId: getSessionId(req) });
  res.json({ job });
});

router.post('/jobs/:jobId/cancel', async (req, res) => {
  const job = jobQueue.cancelJob(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: 'Job not found' });
    return;
  }
  await auditLog.record('job_cancelled', { jobId: job.id, sessionId: getSessionId(req) });
  res.json({ job });
});

router.post('/jobs/:jobId/resume', async (req, res) => {
  const job = jobQueue.resumeJob(req.params.jobId);
  if (!job) {
    res.status(404).json({ error: 'Job not found or not resumable' });
    return;
  }
  await auditLog.record('job_resumed', { jobId: job.id, sessionId: getSessionId(req) });
  res.json({ job });
});

router.get('/accelmat/defaults', (_req, res) => {
  res.json({ defaults: getAccelmatDefaults() });
});

router.get('/accelmat/graphs', async (_req, res, next) => {
  try {
    const graphs = await listAccelmatGraphs();
    res.json({ graphs });
  } catch (error) {
    next(error);
  }
});

router.get('/accelmat/results', async (_req, res, next) => {
  try {
    const results = await listAccelmatResults();
    res.json({ results });
  } catch (error) {
    next(error);
  }
});

router.get('/accelmat/results/:slug', async (req, res, next) => {
  try {
    const result = await readAccelmatResult(sanitizeSlug(req.params.slug));
    res.json({ result });
  } catch (error) {
    res.status(404).json({ error: 'Result not found for this slug' });
  }
});

router.post('/accelmat/run', accelmatRunLimiter, async (req, res, next) => {
  try {
    const { goal, constraints, graphPath, maxRefinementIterations, numHypotheses } = req.body ?? {};
    if (!goal || !graphPath) {
      res.status(400).json({ error: 'goal and graphPath are required' });
      return;
    }
    const slug = sanitizeSlug(req.body?.slug || goal.slice(0, 40));
    const job = jobQueue.createJob({
      type: 'accelmat',
      payload: { slug, goal, constraints: constraints ?? [], graphPath, maxRefinementIterations, numHypotheses },
    });
    await auditLog.record('accelmat_started', {
      jobId: job.id,
      slug,
      sessionId: getSessionId(req),
    });
    res.status(202).json({ job });
  } catch (error) {
    next(error);
  }
});

router.get('/graph', async (_req, res, next) => {
  try {
    const runner = new PipelineRunner();
    const bundle = await runner.getGraphBundle();
    res.json(bundle);
  } catch (error) {
    next(error);
  }
});

router.post('/diagnostics/offline', async (req, res, next) => {
  try {
    const report = await runOfflineDiagnostics();
    await stateStore.update({ lastDiagnosticsReportId: report.reportPath });
    await auditLog.record('diagnostics_run', { reportPath: report.reportPath, sessionId: getSessionId(req) });
    res.json(report);
  } catch (error) {
    next(error);
  }
});

router.get('/diagnostics/offline/latest', async (_req, res, next) => {
  try {
    const report = await getLatestDiagnosticReport();
    res.json({ report });
  } catch (error) {
    next(error);
  }
});

router.get('/diagnostics/offline/history', async (_req, res, next) => {
  try {
    const files = await listDiagnosticReports();
    res.json({ files });
  } catch (error) {
    next(error);
  }
});

router.post('/diagnostics/render-test', (req, res) => {
  const result = runRenderTest(req.body?.graph ?? req.body);
  res.json(result);
});

router.post('/audit/full', async (req, res, next) => {
  try {
    const report = await runFullSystemAudit();
    await stateStore.update({ lastAuditReportPath: report.logPath });
    await auditLog.record('audit_full', { logPath: report.logPath, sessionId: getSessionId(req) });
    res.json(report);
  } catch (error) {
    next(error);
  }
});

router.get('/generated/viewer/status', async (_req, res) => {
  const viewerPath = configManager.resolveProjectPath('viz/data/out/knowledge_graph_viewer.html');
  const graphPath = configManager.resolveProjectPath('viz/data/out/knowledge_graph.html');
  res.json({
    viewerExists: await fileExists(viewerPath),
    graphExists: await fileExists(graphPath),
    viewerPath,
    graphPath,
  });
});

router.post('/hypotheses/generate', async (req, res, next) => {
  try {
    const runner = new PipelineRunner();
    const bundle = await runner.getGraphBundle();
    const defaults = configManager.settings?.hypothesis ?? {};
    const report = generateHypotheses(bundle.graph, { ...defaults, ...(req.body ?? {}) });
    await stateStore.cacheHypotheses(report);
    await auditLog.record('hypotheses_generated', {
      count: report.hypotheses.length,
      sessionId: getSessionId(req),
    });
    res.json(report);
  } catch (error) {
    next(error);
  }
});

router.post('/hypotheses/export', async (req, res, next) => {
  try {
    const { format = 'markdown', labels, ...options } = req.body ?? {};
    const runner = new PipelineRunner();
    const bundle = await runner.getGraphBundle();
    const report = generateHypotheses(bundle.graph, {
      ...configManager.settings?.hypothesis,
      ...options,
    });

    if (format === 'markdown') {
      const markdown = exportHypothesesMarkdown(report, labels);
      res.setHeader('Content-Type', 'text/markdown; charset=utf-8');
      res.setHeader('Content-Disposition', 'attachment; filename="k2-18-hypotheses.md"');
      res.send(markdown);
      return;
    }

    if (format === 'pdf') {
      const pdf = await exportHypothesesPdf(report, labels);
      res.setHeader('Content-Type', 'application/pdf');
      res.setHeader('Content-Disposition', 'attachment; filename="k2-18-hypotheses.pdf"');
      res.send(pdf);
      return;
    }

    res.json(report);
  } catch (error) {
    next(error);
  }
});

router.get('/audit', async (req, res, next) => {
  try {
    const limit = Number(req.query.limit ?? 100);
    const entries = await auditLog.tail(limit);
    res.json({ entries });
  } catch (error) {
    next(error);
  }
});

router.post('/feynman/sessions', feynmanSessionLimiter, async (req, res, next) => {
  try {
    const conversationId = uuidv4();
    await feynmanBridge.createSession(conversationId);
    await auditLog.record('feynman_session_started', { conversationId, sessionId: getSessionId(req) });
    res.status(201).json({ conversationId });
  } catch (error) {
    next(error);
  }
});

router.post('/feynman/sessions/:conversationId/message', feynmanMessageLimiter, async (req, res, next) => {
  try {
    const { message } = req.body ?? {};
    if (!message || typeof message !== 'string') {
      res.status(400).json({ error: 'message is required' });
      return;
    }
    feynmanBridge.sendMessage(req.params.conversationId, message);
    await auditLog.record('feynman_message_sent', {
      conversationId: req.params.conversationId,
      length: message.length,
      sessionId: getSessionId(req),
    });
    res.status(202).json({ accepted: true });
  } catch (error) {
    res.status(404).json({ error: error.message });
  }
});

router.get('/feynman/sessions/:conversationId/stream', (req, res) => {
  const session = feynmanBridge.getSession(req.params.conversationId);
  if (!session) {
    res.status(404).json({ error: 'Feynman session not found' });
    return;
  }

  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
  });

  const send = (event, data) => {
    res.write(`event: ${event}\n`);
    res.write(`data: ${JSON.stringify(data)}\n\n`);
  };

  const onDelta = (data) => send('delta', data);
  const onThinking = (data) => send('thinking', data);
  const onTool = (data) => send('tool', data);
  const onTurnEnd = (data) => send('turn_end', data);
  const onError = (data) => send('error', data);
  const onLog = (data) => send('log', data);
  const onClosed = (data) => {
    send('closed', data);
    cleanup();
    res.end();
  };

  const cleanup = () => {
    session.off('delta', onDelta);
    session.off('thinking', onThinking);
    session.off('tool', onTool);
    session.off('turn_end', onTurnEnd);
    session.off('error', onError);
    session.off('log', onLog);
    session.off('closed', onClosed);
  };

  session.on('delta', onDelta);
  session.on('thinking', onThinking);
  session.on('tool', onTool);
  session.on('turn_end', onTurnEnd);
  session.on('error', onError);
  session.on('log', onLog);
  session.on('closed', onClosed);

  req.on('close', cleanup);
});

router.delete('/feynman/sessions/:conversationId', async (req, res) => {
  feynmanBridge.endSession(req.params.conversationId);
  await auditLog.record('feynman_session_ended', { conversationId: req.params.conversationId, sessionId: getSessionId(req) });
  res.json({ ended: true });
});

router.post('/sessions', async (req, res, next) => {
  try {
    const session = await sessionManager.create(req.body?.state ?? {});
    res.status(201).json({ session });
  } catch (error) {
    next(error);
  }
});

router.get('/sessions/:sessionId', async (req, res, next) => {
  try {
    const session = await sessionManager.get(req.params.sessionId);
    if (!session) {
      res.status(404).json({ error: 'Session not found' });
      return;
    }
    res.json({ session });
  } catch (error) {
    next(error);
  }
});

router.patch('/sessions/:sessionId', async (req, res, next) => {
  try {
    const session = await sessionManager.update(req.params.sessionId, req.body?.state ?? {});
    if (!session) {
      res.status(404).json({ error: 'Session not found' });
      return;
    }
    res.json({ session });
  } catch (error) {
    next(error);
  }
});

export default router;
