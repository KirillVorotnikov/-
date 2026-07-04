import { useEffect, useState } from 'react';

import { useTranslation } from 'react-i18next';

import { api, type DiagnosticStep } from '../api/client';



export function DiagnosticsView() {

  const { t } = useTranslation();

  const [steps, setSteps] = useState<DiagnosticStep[]>([]);

  const [summary, setSummary] = useState<Record<string, number>>({});

  const [loading, setLoading] = useState(false);

  const [error, setError] = useState<string | null>(null);

  const [hasRun, setHasRun] = useState(false);



  useEffect(() => {

    api.getLatestDiagnostics()

      .then(({ report }) => {

        if (report?.steps?.length) {

          setSteps(report.steps);

          setSummary(report.summary ?? {});

          setHasRun(true);

        }

      })

      .catch(() => {});

  }, []);



  async function runDiagnostics() {

    setLoading(true);

    setError(null);

    try {

      const report = await api.runDiagnostics();

      setSteps(report.steps ?? []);

      setSummary(report.summary ?? {});

      setHasRun(true);

    } catch (err) {

      setError(err instanceof Error ? err.message : t('diagnostics.run_failed'));

    } finally {

      setLoading(false);

    }

  }



  return (

    <div className="panel">

      <div className="pipeline-header">

        <h2>{t('diagnostics.title')}</h2>

        <button type="button" className="btn-primary" onClick={runDiagnostics} disabled={loading}>

          {loading ? t('common.loading') : t('diagnostics.run')}

        </button>

      </div>



      <p className="hint">{t('diagnostics.description')}</p>



      {error && <div className="status-banner warn">{error}</div>}



      {hasRun && summary.pass !== undefined && (

        <p className="diag-summary">{t('diagnostics.summary', { pass: summary.pass, warn: summary.warn, fail: summary.fail })}</p>

      )}



      {!hasRun && !loading && (

        <p className="hint">{t('diagnostics.no_report')}</p>

      )}



      {steps.map((step) => (

        <div key={step.id} className="diag-step">

          <div className={`status-dot ${step.status}`} />

          <div className="diag-step-body">

            <strong>{t(`diagnostics.steps.${step.id}`, { defaultValue: step.id })}</strong>

            <span className="diag-status">{t(`diagnostics.status_${step.status}`)}</span>

            {step.details && Object.keys(step.details).length > 0 && (

              <details className="diag-details">

                <summary>{t('diagnostics.details')}</summary>

                <pre>{JSON.stringify(step.details, null, 2)}</pre>

              </details>

            )}

          </div>

        </div>

      ))}

    </div>

  );

}



export function AuditView() {

  const { t } = useTranslation();

  const [entries, setEntries] = useState<{ timestamp: string; event: string; [key: string]: unknown }[]>([]);



  useEffect(() => {

    api.getAudit().then(({ entries: e }) => setEntries(e)).catch(() => {});

  }, []);



  return (

    <div className="panel">

      <h2>{t('audit.title')}</h2>

      <table className="audit-table">

        <thead>

          <tr>

            <th>{t('audit.time')}</th>

            <th>{t('audit.event')}</th>

            <th>{t('audit.details')}</th>

          </tr>

        </thead>

        <tbody>

          {entries.slice().reverse().map((entry, i) => (

            <tr key={i}>

              <td>{entry.timestamp}</td>

              <td>{entry.event}</td>

              <td>{JSON.stringify({ ...entry, timestamp: undefined, event: undefined })}</td>

            </tr>

          ))}

        </tbody>

      </table>

    </div>

  );

}


