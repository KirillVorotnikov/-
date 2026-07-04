import { useCallback, useEffect, useRef, useState } from 'react';

import { useTranslation } from 'react-i18next';

import { api, type Job } from '../api/client';

import { ModeKpiCard } from './ModeIndicator';

import type { ConfigStatus } from '../utils/modeLabel';



const PIPELINE_STAGES = ['slicer', 'concepts', 'graph', 'refiner', 'metrics'];



export function PipelineView() {

  const { t } = useTranslation();

  const [activeJob, setActiveJob] = useState<Job | null>(null);

  const [log, setLog] = useState('');

  const [integrationMode, setIntegrationMode] = useState<'new' | 'incremental'>('new');

  const streamCleanup = useRef<(() => void) | null>(null);



  useEffect(() => {

    api.getConfigStatus().then((cfg) => {

      const mode = cfg.integrationMode as string | undefined;

      if (mode === 'incremental' || mode === 'new') setIntegrationMode(mode);

    }).catch(() => {});



    api.listJobs().then(({ jobs }) => {

      const running = jobs.find((j) => j.status === 'running');

      if (running) attachJob(running.id);

    }).catch(() => {});



    return () => streamCleanup.current?.();

  }, []);



  function attachJob(jobId: string) {

    streamCleanup.current?.();

    streamCleanup.current = subscribe(jobId);

  }



  function subscribe(jobId: string) {

    const source = new EventSource(`/api/jobs/${jobId}/stream`);

    source.addEventListener('snapshot', (e) => {

      const job = JSON.parse(e.data) as Job;

      setActiveJob(job);

      setLog(job.logs.map((l) => l.message).join('\n'));

    });

    source.addEventListener('update', (e) => {

      const job = JSON.parse(e.data) as Job;

      setActiveJob(job);

    });

    source.addEventListener('log', (e) => {

      const entry = JSON.parse(e.data) as { message: string };

      setLog((prev) => `${prev}${entry.message}\n`);

    });

    return () => source.close();

  }



  async function runStages(stages?: string[]) {

    const incremental = integrationMode === 'incremental';

    const { job } = await api.runPipeline(stages, incremental);

    setActiveJob(job);

    setLog('');

    attachJob(job.id);

  }



  return (

    <div className="panel pipeline-panel">

      <div className="pipeline-header">

        <h2>{t('pipeline.title')}</h2>

        <div className="pipeline-actions">

          <button type="button" className="btn-primary" onClick={() => runStages()}>{t('pipeline.run_full')}</button>

          {activeJob && ['running', 'paused'].includes(activeJob.status) && (

            <>

              <button type="button" className="btn-secondary" onClick={() => api.pauseJob(activeJob.id)}>{t('pipeline.pause')}</button>

              <button type="button" className="btn-danger" onClick={() => api.cancelJob(activeJob.id)}>{t('pipeline.cancel')}</button>

            </>

          )}

        </div>

      </div>



      <div className={`integration-banner${integrationMode === 'incremental' ? ' append' : ''}`}>

        <strong>{integrationMode === 'incremental' ? t('dashboard.integration_append') : t('dashboard.integration_new')}</strong>

        <span>{integrationMode === 'incremental' ? t('pipeline.append_hint') : t('pipeline.new_hint')}</span>

      </div>



      <div className="stage-track">

        {PIPELINE_STAGES.map((stage) => {

          const stageIndex = PIPELINE_STAGES.indexOf(stage);

          const activeIndex = activeJob?.stage ? PIPELINE_STAGES.indexOf(activeJob.stage) : -1;

          const isDone = activeJob && activeIndex > stageIndex;

          const isActive = activeJob?.stage === stage;

          return (

            <button

              key={stage}

              type="button"

              className={`stage-chip${isActive ? ' active' : ''}${isDone ? ' done' : ''}`}

              onClick={() => runStages([stage])}

              disabled={activeJob?.status === 'running'}

            >

              {t(`pipeline.stages.${stage}`)}

            </button>

          );

        })}

      </div>



      {activeJob && (

        <div className="pipeline-status">

          <span>{activeJob.status}</span>

          <div className="progress-bar">

            <div className="progress-fill" style={{ width: `${activeJob.progress}%` }} />

          </div>

          <span>{activeJob.progress}%</span>

        </div>

      )}



      <pre className="log-console">{log || t('pipeline.log_placeholder')}</pre>

    </div>

  );

}



export function DashboardView({ config, onConfigRefresh }: { config: ConfigStatus; onConfigRefresh?: () => void }) {

  const { t } = useTranslation();

  const [files, setFiles] = useState<{ name: string; size: number }[]>([]);

  const [graphStats, setGraphStats] = useState({ nodes: 0, edges: 0 });

  const [integrationMode, setIntegrationMode] = useState<'new' | 'incremental'>('new');

  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);

  const [uploading, setUploading] = useState(false);

  const [uploadMessage, setUploadMessage] = useState<{ type: 'ok' | 'err'; text: string } | null>(null);

  const [dragOver, setDragOver] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);



  const refreshData = useCallback(async () => {

    const [f, g, cfg] = await Promise.all([api.listFiles(), api.getGraph(), api.getConfigStatus()]);

    setFiles(f.files);

    setGraphStats({ nodes: g.loadStatus.nodeCount, edges: g.loadStatus.edgeCount });

    const mode = cfg.integrationMode as string | undefined;

    if (mode === 'incremental' || mode === 'new') setIntegrationMode(mode);

  }, []);



  useEffect(() => {

    refreshData().catch(() => {});

    onConfigRefresh?.();

  }, [onConfigRefresh, refreshData]);



  function pickFiles(fileList: FileList | null) {

    if (!fileList?.length) return;

    const allowed = ['.txt', '.md', '.html'];

    const picked = [...fileList].filter((f) => allowed.some((ext) => f.name.toLowerCase().endsWith(ext)));

    if (picked.length === 0) {

      setUploadMessage({ type: 'err', text: t('dashboard.upload_invalid') });

      return;

    }

    setSelectedFiles((prev) => {

      const names = new Set(prev.map((f) => f.name));

      return [...prev, ...picked.filter((f) => !names.has(f.name))];

    });

    setUploadMessage(null);

  }



  async function handleUpload() {

    if (selectedFiles.length === 0) {

      setUploadMessage({ type: 'err', text: t('dashboard.upload_empty') });

      return;

    }

    setUploading(true);

    setUploadMessage(null);

    try {

      await api.ensureSession();

      const dt = new DataTransfer();

      selectedFiles.forEach((f) => dt.items.add(f));

      await api.uploadFiles(dt.files, integrationMode);

      setSelectedFiles([]);

      await refreshData();

      setUploadMessage({ type: 'ok', text: t('dashboard.upload_success') });

    } catch {

      setUploadMessage({ type: 'err', text: t('dashboard.upload_failed') });

    } finally {

      setUploading(false);

    }

  }



  return (

    <>

      <div className="kpi-grid">

        <ModeKpiCard config={config} />

        <div className="kpi-card">

          <small>{t('dashboard.kpi_files')}</small>

          <strong>{files.length}</strong>

        </div>

        <div className="kpi-card">

          <small>{t('dashboard.kpi_nodes')}</small>

          <strong>{graphStats.nodes}</strong>

          <small>{t('dashboard.kpi_edges', { count: graphStats.edges })}</small>

        </div>

      </div>



      <div className="panel upload-panel">

        <h2>{t('dashboard.upload_title')}</h2>



        <div

          className={`upload-dropzone${dragOver ? ' drag-over' : ''}`}

          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}

          onDragLeave={() => setDragOver(false)}

          onDrop={(e) => {

            e.preventDefault();

            setDragOver(false);

            pickFiles(e.dataTransfer.files);

          }}

          onClick={() => fileInputRef.current?.click()}

          role="button"

          tabIndex={0}

          onKeyDown={(e) => e.key === 'Enter' && fileInputRef.current?.click()}

        >

          <p className="upload-hint">{t('dashboard.upload_hint')}</p>

          <p className="upload-formats">{t('dashboard.upload_formats')}</p>

          <button type="button" className="btn-secondary upload-browse" onClick={(e) => { e.stopPropagation(); fileInputRef.current?.click(); }}>

            {t('dashboard.upload_browse')}

          </button>

          <input

            ref={fileInputRef}

            type="file"

            multiple

            accept=".txt,.md,.html"

            hidden

            onChange={(e) => pickFiles(e.target.files)}

          />

        </div>



        {selectedFiles.length > 0 && (

          <ul className="upload-queue">

            {selectedFiles.map((f) => (

              <li key={f.name}>

                <span>{f.name}</span>

                <span className="upload-size">{(f.size / 1024).toFixed(1)} KB</span>

                <button type="button" className="upload-remove" onClick={() => setSelectedFiles((prev) => prev.filter((x) => x.name !== f.name))}>×</button>

              </li>

            ))}

          </ul>

        )}



        <div className="integration-options">

          <label className={`integration-option${integrationMode === 'new' ? ' selected' : ''}`}>

            <input type="radio" name="integration" value="new" checked={integrationMode === 'new'} onChange={() => setIntegrationMode('new')} />

            <span>

              <strong>{t('dashboard.integration_new')}</strong>

              <small>{t('dashboard.integration_new_hint')}</small>

            </span>

          </label>

          <label className={`integration-option${integrationMode === 'incremental' ? ' selected' : ''}`}>

            <input type="radio" name="integration" value="incremental" checked={integrationMode === 'incremental'} onChange={() => setIntegrationMode('incremental')} />

            <span>

              <strong>{t('dashboard.integration_append')}</strong>

              <small>{t('dashboard.integration_append_hint')}</small>

            </span>

          </label>

        </div>



        <button type="button" className="btn-primary" onClick={handleUpload} disabled={uploading || selectedFiles.length === 0}>

          {uploading ? t('common.loading') : t('dashboard.upload_btn')}

        </button>



        {uploadMessage && (

          <p className={`upload-message ${uploadMessage.type}`}>{uploadMessage.text}</p>

        )}



        {files.length > 0 && (

          <div className="corpus-list">

            <h3>{t('dashboard.corpus_files')}</h3>

            <ul>

              {files.map((f) => (

                <li key={f.name}>

                  <span>{f.name}</span>

                  <span>{(f.size / 1024).toFixed(1)} KB</span>

                </li>

              ))}

            </ul>

          </div>

        )}

      </div>

    </>

  );

}


