import express from 'express';
import path from 'node:path';
import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import apiRouter from './routes/api.js';
import { configManager } from './services/configManager.js';
import { jobQueue } from './services/jobQueue.js';
import { auditLog } from './services/auditLog.js';
import { sessionManager } from './services/sessionManager.js';
import { stateStore } from './services/stateStore.js';
import { warnIfApiKeyMissing } from './middleware/security.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = path.resolve(__dirname, '..');
const DIST_DIR = path.join(WEB_ROOT, 'dist');
const PUBLIC_DIR = path.join(WEB_ROOT, 'public');
const VIZ_VENDOR_DIR = path.resolve(WEB_ROOT, '..', 'viz', 'vendor');
const VIZ_STATIC_DIR = path.resolve(WEB_ROOT, '..', 'viz', 'static');
const PROJECT_ROOT = path.resolve(WEB_ROOT, '..');

const app = express();

app.use(express.json({ limit: '4mb' }));
app.use('/vendor', express.static(VIZ_VENDOR_DIR));
app.use('/viz-static', express.static(VIZ_STATIC_DIR));

app.use('/generated/viewer', express.static(path.join(PROJECT_ROOT, 'viz/data/out'), {
  index: 'knowledge_graph_viewer.html',
}));

app.use('/generated/graph', express.static(path.join(PROJECT_ROOT, 'viz/data/out'), {
  index: 'knowledge_graph.html',
}));

app.use('/api', apiRouter);

async function resolveStaticDir() {
  try {
    await fs.access(path.join(DIST_DIR, 'index.html'));
    return DIST_DIR;
  } catch {
    return PUBLIC_DIR;
  }
}

app.use(async (req, res, next) => {
  if (req.path.startsWith('/api') || req.path.startsWith('/vendor') || req.path.startsWith('/viz-static') || req.path.startsWith('/generated')) {
    next();
    return;
  }
  const staticDir = await resolveStaticDir();
  express.static(staticDir)(req, res, next);
});

app.use((error, req, res, _next) => {
  const message = error?.message ?? 'Internal server error';
  const status = error?.message?.includes('Unsupported file type') ? 400 : 500;
  console.error(`[${new Date().toISOString()}] ERROR ${req.method} ${req.path}:`, message);
  res.status(status).json({ error: message });
});

app.get('*', async (_req, res) => {
  const staticDir = await resolveStaticDir();
  res.sendFile(path.join(staticDir, 'index.html'));
});

async function bootstrap() {
  await configManager.init();
  await stateStore.init();
  await jobQueue.init();
  await auditLog.init();
  await sessionManager.init();

  warnIfApiKeyMissing();

  const port = configManager.settings?.port ?? 3847;
  app.listen(port, () => {
    console.log(`K2-18 web dashboard running at http://localhost:${port}`);
    console.log(`Project root: ${configManager.getProjectRoot()}`);
    console.log(`Mode: ${configManager.getMode()} (${configManager.getProviderForMode()})`);
  });
}

bootstrap().catch((error) => {
  console.error('Failed to start K2-18 web server:', error);
  process.exit(1);
});
