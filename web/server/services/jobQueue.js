import fs from 'node:fs/promises';
import path from 'node:path';
import { EventEmitter } from 'node:events';
import { v4 as uuidv4 } from 'uuid';
import { configManager } from './configManager.js';
import { PipelineRunner, STAGE_LABELS } from './pipelineRunner.js';
import { runOfflineDiagnostics } from './diagnosticsService.js';
import { stateStore } from './stateStore.js';
import { writeRequest as writeAccelmatRequest, runAccelmat, readResult as readAccelmatResult } from './accelmatRunner.js';

const JOBS_DIR = path.join(configManager.getWebRoot(), 'runtime', 'jobs');
const CHECKPOINTS_DIR = path.join(configManager.getWebRoot(), 'runtime', 'checkpoints');
// Paths resolved via configManager after init

/**
 * In-memory task queue with disk persistence for recovery.
 */
export class JobQueue extends EventEmitter {
  constructor() {
    super();
    this.jobs = new Map();
    this.runningJobId = null;
    this.runner = null;
  }

  async init() {
    await fs.mkdir(JOBS_DIR, { recursive: true });
    await fs.mkdir(CHECKPOINTS_DIR, { recursive: true });
    await this.loadPersistedJobs();
  }

  async loadPersistedJobs() {
    const files = await fs.readdir(JOBS_DIR).catch(() => []);
    for (const file of files) {
      if (!file.endsWith('.json')) continue;
      const raw = await fs.readFile(path.join(JOBS_DIR, file), 'utf8');
      const job = JSON.parse(raw);
      if (['running', 'paused'].includes(job.status)) {
        job.status = 'interrupted';
        job.error = 'Recovered after server restart';
      }
      this.jobs.set(job.id, job);
    }
  }

  async persistJob(job) {
    const target = path.join(JOBS_DIR, `${job.id}.json`);
    await fs.writeFile(target, JSON.stringify(job, null, 2), 'utf8');
  }

  listJobs() {
    return [...this.jobs.values()].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }

  getJob(jobId) {
    return this.jobs.get(jobId) ?? null;
  }

  createJob({ type, payload }) {
    const job = {
      id: uuidv4(),
      type,
      payload,
      status: 'queued',
      progress: 0,
      stage: null,
      logs: [],
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      completedAt: null,
      error: null,
      checkpoint: null,
      result: null,
    };
    this.jobs.set(job.id, job);
    this.persistJob(job);
    this.emit('job:created', job);
    this.processNext();
    return job;
  }

  pauseJob(jobId) {
    const job = this.getJob(jobId);
    if (!job || job.status !== 'running') return null;
    job.status = 'paused';
    job.updatedAt = new Date().toISOString();
    this.runner?.requestCancel();
    this.persistJob(job);
    this.emit('job:updated', job);
    return job;
  }

  cancelJob(jobId) {
    const job = this.getJob(jobId);
    if (!job) return null;
    if (job.status === 'running') {
      this.runner?.requestCancel();
    }
    job.status = 'cancelled';
    job.updatedAt = new Date().toISOString();
    job.completedAt = new Date().toISOString();
    this.persistJob(job);
    this.emit('job:updated', job);
    return job;
  }

  resumeJob(jobId) {
    const job = this.getJob(jobId);
    if (!job || !['paused', 'interrupted', 'failed'].includes(job.status)) return null;
    job.status = 'queued';
    job.error = null;
    job.updatedAt = new Date().toISOString();
    this.persistJob(job);
    this.emit('job:updated', job);
    this.processNext();
    return job;
  }

  appendLog(job, entry) {
    job.logs.push({ ...entry, timestamp: new Date().toISOString() });
    if (job.logs.length > 500) {
      job.logs = job.logs.slice(-500);
    }
    job.updatedAt = new Date().toISOString();
    this.persistJob(job);
    this.emit('job:log', job, entry);
  }

  async saveCheckpoint(job, stage) {
    const checkpoint = {
      jobId: job.id,
      stage,
      savedAt: new Date().toISOString(),
    };
    job.checkpoint = checkpoint;
    const target = path.join(CHECKPOINTS_DIR, `${job.id}.json`);
    await fs.writeFile(target, JSON.stringify(checkpoint, null, 2), 'utf8');
  }

  async processNext() {
    if (this.runningJobId) return;

    const nextJob = this.listJobs().find((job) => job.status === 'queued');
    if (!nextJob) return;

    this.runningJobId = nextJob.id;
    nextJob.status = 'running';
    nextJob.updatedAt = new Date().toISOString();
    await this.persistJob(nextJob);
    this.emit('job:updated', nextJob);

    this.runner = new PipelineRunner((entry) => this.appendLog(nextJob, entry));

    try {
      if (nextJob.type === 'pipeline') {
        await this.runPipelineJob(nextJob);
      } else if (nextJob.type === 'accelmat') {
        await this.runAccelmatJob(nextJob);
      } else {
        throw new Error(`Unsupported job type: ${nextJob.type}`);
      }

      nextJob.status = 'completed';
      nextJob.progress = 100;
      nextJob.completedAt = new Date().toISOString();
      if (nextJob.type === 'pipeline') {
        await stateStore.recordPipelineComplete(nextJob);
        if (configManager.getMode() === 'offline') {
          try {
            const diagReport = await runOfflineDiagnostics();
            nextJob.diagnosticsReportPath = diagReport.reportPath;
            await stateStore.update({ lastDiagnosticsReportId: diagReport.reportPath });
          } catch (diagError) {
            this.appendLog(nextJob, { level: 'warn', message: `Diagnostics failed: ${diagError.message}` });
          }
        }
      }
    } catch (error) {
      nextJob.status = nextJob.status === 'paused' ? 'paused' : 'failed';
      nextJob.error = error.message;
    } finally {
      nextJob.updatedAt = new Date().toISOString();
      await this.persistJob(nextJob);
      this.emit('job:updated', nextJob);
      this.runningJobId = null;
      this.runner = null;
      this.processNext();
    }
  }

  async runPipelineJob(job) {
    const incremental = job.payload?.incremental ?? false;
    const defaultStages = configManager.settings.pipeline.stages;
    const stages = job.payload?.stages ?? defaultStages;
    const startIndex = job.checkpoint?.stage
      ? Math.max(0, stages.indexOf(job.checkpoint.stage) + 1)
      : 0;

    if (startIndex === 0 && !incremental) {
      await this.runner.clearPipelineArtifacts();
      this.appendLog(job, {
        level: 'info',
        message: 'Full rebuild: cleared staging and output directories',
      });
    } else if (startIndex === 0 && incremental) {
      this.appendLog(job, {
        level: 'info',
        message: 'Incremental mode: preserving existing graph artifacts',
      });
    }

    const remaining = stages.slice(startIndex);

    for (let index = 0; index < remaining.length; index += 1) {
      if (job.status === 'paused' || job.status === 'cancelled') {
        break;
      }

      const stage = remaining[index];
      job.stage = stage;
      job.progress = Math.round(((startIndex + index) / stages.length) * 100);
      this.appendLog(job, {
        level: 'info',
        stage,
        message: `Stage queued: ${STAGE_LABELS[stage] ?? stage}`,
      });

      if (stage === 'metrics') {
        await this.runner.copyArtifactsForViz();
      }

      await this.runner.runStage(stage);
      await this.saveCheckpoint(job, stage);
      job.progress = Math.round(((startIndex + index + 1) / stages.length) * 100);
      await this.persistJob(job);
    }

    if (
      !stages.includes('metrics') &&
      configManager.settings.pipeline.autoRunMetrics &&
      job.status === 'running'
    ) {
      await this.runner.copyArtifactsForViz();
      await this.runner.runStage('metrics');
    }
  }

  async runAccelmatJob(job) {
    const { slug, graphPath, goal, constraints, maxRefinementIterations, numHypotheses } = job.payload ?? {};
    if (!slug) {
      throw new Error('accelmat job payload is missing "slug"');
    }

    job.progress = 5;
    this.appendLog(job, { level: 'info', message: `Writing ACCELMAT request for slug "${slug}"` });
    await writeAccelmatRequest(slug, { graphPath, goal, constraints, maxRefinementIterations, numHypotheses });
    await this.persistJob(job);

    job.progress = 10;
    await this.persistJob(job);
    await runAccelmat(slug, (entry) => this.appendLog(job, entry));

    job.progress = 95;
    this.appendLog(job, { level: 'info', message: 'ACCELMAT run finished, reading result JSON' });
    job.result = await readAccelmatResult(slug);
    await this.persistJob(job);
  }
}

export const jobQueue = new JobQueue();
