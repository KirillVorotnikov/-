import cytoscape from 'cytoscape';
import { useRef, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api, type Hypothesis } from '../api/client';

function MiniGraph({ hypothesis }: { hypothesis: Hypothesis }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current || !hypothesis.subgraph?.nodes?.length) return;
    const cy = cytoscape({
      container: ref.current,
      elements: [
        ...hypothesis.subgraph.nodes.map((n) => ({
          data: { id: n.id, label: (n.name ?? n.text ?? n.id).slice(0, 12) },
        })),
        ...(hypothesis.subgraph.edges ?? []).map((e, i) => ({
          data: {
            id: `e${i}`,
            source: e.source ?? e.from,
            target: e.target ?? e.to,
          },
        })),
      ],
      style: [
        { selector: 'node', style: { label: 'data(label)', 'font-size': 7, width: 18, height: 18, 'background-color': '#4a90d9' } },
        { selector: 'edge', style: { width: 1, 'line-color': '#64748b', 'target-arrow-shape': 'triangle', 'target-arrow-color': '#64748b', 'curve-style': 'bezier' } },
      ],
      layout: { name: 'circle', fit: true, padding: 10 },
      userZoomingEnabled: false,
      userPanningEnabled: false,
    });
    return () => cy.destroy();
  }, [hypothesis]);

  if (!hypothesis.subgraph?.nodes?.length) return null;
  return <div ref={ref} className="mini-graph" />;
}

export function HypothesesView() {
  const { t } = useTranslation();
  const [hypotheses, setHypotheses] = useState<Hypothesis[]>([]);
  const [targetProperty, setTargetProperty] = useState('');
  const [minConfidence, setMinConfidence] = useState(0.4);
  const [maxResults, setMaxResults] = useState(20);
  const [loading, setLoading] = useState(false);

  async function generate() {
    setLoading(true);
    try {
      const report = await api.generateHypotheses({ targetProperty, minConfidence, maxResults });
      setHypotheses(report.hypotheses);
    } finally {
      setLoading(false);
    }
  }

  async function exportFile(format: 'markdown' | 'pdf') {
    const labels = {
      title: t('hypotheses.export_title'),
      generated: t('hypotheses.export_generated'),
      category: t('hypotheses.export_category'),
      confidence: t('hypotheses.confidence'),
      summary: t('hypotheses.export_summary'),
      reasoning: t('hypotheses.export_reasoning'),
      experiments: t('hypotheses.export_experiments_label'),
    };
    const blob = await api.exportHypotheses(format, { targetProperty, minConfidence, maxResults }, labels);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = format === 'pdf' ? 'k2-18-hypotheses.pdf' : 'k2-18-hypotheses.md';
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="panel">
      <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.5rem' }}>
        <h2>{t('hypotheses.title')}</h2>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button type="button" className="btn-primary" onClick={generate} disabled={loading}>{t('hypotheses.generate')}</button>
          <button type="button" className="btn-secondary" onClick={() => exportFile('markdown')}>{t('hypotheses.export_md')}</button>
          <button type="button" className="btn-secondary" onClick={() => exportFile('pdf')}>{t('hypotheses.export_pdf')}</button>
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '0.75rem', margin: '1rem 0' }}>
        <label>{t('hypotheses.target_property')}<input value={targetProperty} onChange={(e) => setTargetProperty(e.target.value)} style={{ width: '100%' }} /></label>
        <label>{t('hypotheses.min_confidence')}<input type="number" min={0} max={1} step={0.05} value={minConfidence} onChange={(e) => setMinConfidence(Number(e.target.value))} style={{ width: '100%' }} /></label>
        <label>{t('hypotheses.max_results')}<input type="number" min={1} max={50} value={maxResults} onChange={(e) => setMaxResults(Number(e.target.value))} style={{ width: '100%' }} /></label>
      </div>
      {hypotheses.length === 0 && <p>{t('hypotheses.empty')}</p>}
      {hypotheses.map((item) => (
        <article key={item.id} className="hypothesis-card">
          <span style={{ background: 'var(--accent-blue)', color: '#fff', padding: '0.15rem 0.5rem', borderRadius: 999, fontSize: '0.75rem' }}>
            {(item.confidence * 100).toFixed(0)}% {t('hypotheses.confidence').toLowerCase()}
          </span>
          <h3>{item.title}</h3>
          <p>{item.summary}</p>
          <p><strong>{t('hypotheses.experiments')}:</strong></p>
          <ul>{item.suggestedExperiments.map((exp, i) => <li key={i}>{exp}</li>)}</ul>
          {item.relatedConcepts && item.relatedConcepts.length > 0 && (
            <p><strong>{t('hypotheses.related')}:</strong> {item.relatedConcepts.join(', ')}</p>
          )}
          <MiniGraph hypothesis={item} />
        </article>
      ))}
    </div>
  );
}
