import { useEffect, useState } from 'react';

import { useTranslation } from 'react-i18next';

import { api } from './api/client';

import { ModeIndicator } from './components/ModeIndicator';

import { DashboardView, PipelineView } from './components/PipelineDashboard';

import { GraphView } from './components/GraphView';

import { DiagnosticsView, AuditView } from './components/DiagnosticsSettings';

import { HypothesesView } from './components/HypothesesView';

import { AccelmatView } from './components/AccelmatView';

import { FeynmanChat } from './components/FeynmanChat';

import type { ConfigStatus } from './utils/modeLabel';



type ViewId = 'dashboard' | 'pipeline' | 'graph' | 'hypotheses' | 'accelmat' | 'feynman' | 'diagnostics' | 'audit';



const NAV: ViewId[] = ['dashboard', 'pipeline', 'graph', 'hypotheses', 'accelmat', 'feynman', 'diagnostics', 'audit'];



export default function App() {

  const { t } = useTranslation();

  const [view, setView] = useState<ViewId>('dashboard');

  const [config, setConfig] = useState<ConfigStatus>({});

  const [toast, setToast] = useState('');

  const [modeWarning, setModeWarning] = useState(false);

  const [pendingMode, setPendingMode] = useState<string | null>(null);

  const [discussContext, setDiscussContext] = useState<{ slug: string; suggestionKey: string; goal: string } | null>(null);

  function handleDiscussHypothesis(context: { slug: string; suggestionKey: string; goal: string }) {
    setDiscussContext(context);
    setView('feynman');
  }



  useEffect(() => {

    api.ensureSession().catch(() => {});

    const theme = 'light';
    document.documentElement.dataset.theme = theme;
    localStorage.setItem('k2-18-theme', theme);

    refreshConfig();

  }, []);



  async function refreshConfig() {

    const cfg = await api.getConfigStatus();

    setConfig(cfg as ConfigStatus);

  }



  async function switchMode(mode: string, force = false) {

    try {

      await api.setMode(mode, force);

      await refreshConfig();

      setModeWarning(false);

      setPendingMode(null);

      showToast(mode === 'offline' ? t('mode.offline') : t('mode.online'));

    } catch (err) {

      const message = err instanceof Error ? err.message : '';

      if (message.includes('mode_switch_warning')) {

        setModeWarning(true);

        setPendingMode(mode);

      } else {

        showToast(t('errors.generic'));

      }

    }

  }



  function showToast(msg: string) {

    setToast(msg);

    setTimeout(() => setToast(''), 3000);

  }



  return (

    <div className="app-shell">

      <header className="top-bar">

        <nav className="sidebar sidebar-inline">

          {NAV.map((id) => (

            <button key={id} type="button" className={`nav-item${view === id ? ' active' : ''}`} onClick={() => setView(id)}>

              {t(`nav.${id === 'graph' ? 'graph' : id}`)}

            </button>

          ))}

        </nav>

        <div className="top-actions">

          <ModeIndicator config={config} />

          <div className="mode-switch">

            <button type="button" className={`mode-btn${config.mode === 'online' ? ' active' : ''}`} onClick={() => switchMode('online')}>

              {t('mode.online_short')}

            </button>

            <button type="button" className={`mode-btn${config.mode === 'offline' ? ' active' : ''}`} onClick={() => switchMode('offline')}>

              {t('mode.offline_short')}

            </button>

          </div>

        </div>

      </header>



      <main className="content">

        {view === 'dashboard' && <DashboardView config={config} onConfigRefresh={refreshConfig} />}

        {view === 'pipeline' && <PipelineView />}

        {view === 'graph' && <GraphView active={view === 'graph'} />}

        {view === 'hypotheses' && <HypothesesView />}

        {view === 'accelmat' && <AccelmatView onDiscussHypothesis={handleDiscussHypothesis} />}

        {view === 'feynman' && <FeynmanChat seedContext={discussContext} onNavigateToAccelmat={() => setView('accelmat')} />}

        {view === 'diagnostics' && <DiagnosticsView />}

        {view === 'audit' && <AuditView />}

      </main>



      {modeWarning && (

        <div className="toast" style={{ bottom: '4rem' }}>

          {t('mode.regeneration_warning')}

          <button type="button" className="btn-secondary" style={{ marginLeft: '0.5rem' }} onClick={() => pendingMode && switchMode(pendingMode, true)}>

            {t('mode.confirm_switch')}

          </button>

        </div>

      )}

      {toast && <div className="toast">{toast}</div>}

    </div>

  );

}


