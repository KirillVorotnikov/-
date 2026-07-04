import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api, type GraphBundle, type GraphNode } from '../api/client';
import { useCytoscapeGraph, EDGE_STYLES } from '../hooks/useCytoscapeGraph';
import { formatNumber } from '../utils/format';

interface Props {
  active: boolean;
}

export function GraphView({ active }: Props) {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement>(null);
  const [bundle, setBundle] = useState<GraphBundle | null>(null);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [viewMode, setViewMode] = useState('all');
  const [search, setSearch] = useState('');
  const [vizConfig, setVizConfig] = useState<{ nodeColors: Record<string, string> } | null>(null);
  const [enabledTypes, setEnabledTypes] = useState<Set<string>>(new Set());
  const [stats, setStats] = useState({ total: 0, visible: 0, edges: 0 });
  const [loadError, setLoadError] = useState<string | null>(null);
  const [graphReady, setGraphReady] = useState(false);

  const colors = vizConfig?.nodeColors;

  const { initGraph, resizeAndFit, applyViewMode, applyTypeFilter, search: doSearch, setOnSelect, getStats, destroy } =
    useCytoscapeGraph(containerRef, colors);

  const edgeTypes = bundle
    ? [...new Set((bundle.graph.edges ?? []).map((e) => e.type ?? e.relationship ?? 'default'))].sort()
    : [];

  useEffect(() => {
    setOnSelect(setSelected);
  }, [setOnSelect]);

  useEffect(() => {
    if (!active) return;
    setLoadError(null);
    Promise.all([api.getGraph(), api.getVizConfig()])
      .then(([graphBundle, viz]) => {
        setBundle(graphBundle);
        setVizConfig(viz);
        setEnabledTypes(new Set(Object.keys(graphBundle.loadStatus.nodeTypes)));
      })
      .catch((err) => setLoadError(err instanceof Error ? err.message : 'load_failed'));
  }, [active]);

  useEffect(() => {
    if (!active || !bundle) return;

    const timer = window.setTimeout(() => {
      const ok = initGraph(bundle);
      setGraphReady(ok);
      setStats(getStats());
      if (!ok && (bundle.loadStatus.nodeCount ?? 0) > 0) {
        setLoadError('cytoscape_init_failed');
      }
    }, 50);

    return () => window.clearTimeout(timer);
  }, [active, bundle, initGraph, getStats]);

  useEffect(() => {
    if (active && graphReady) {
      resizeAndFit();
    }
  }, [active, graphReady, resizeAndFit]);

  useEffect(() => {
    if (!active) destroy();
  }, [active, destroy]);

  useEffect(() => {
    applyViewMode(viewMode);
    setStats(getStats());
  }, [viewMode, applyViewMode, getStats]);

  useEffect(() => {
    if (enabledTypes.size) applyTypeFilter(enabledTypes);
    setStats(getStats());
  }, [enabledTypes, applyTypeFilter, getStats]);

  useEffect(() => {
    doSearch(search);
  }, [search, doSearch]);

  const loadStatus = bundle?.loadStatus;
  const shortPath = loadStatus?.graphPath?.split(/[/\\]/).slice(-3).join('/') ?? '';

  return (
    <div className="graph-view">
      {loadError && (
        <div className="status-banner warn">{t('errors.load_graph')}: {loadError}</div>
      )}

      <div className="graph-layout">
        <aside className="graph-sidebar">
          <h3>{t('graph.filters')}</h3>
          <select value={viewMode} onChange={(e) => setViewMode(e.target.value)} className="graph-select">
            <option value="all">{t('graph.view_all')}</option>
            <option value="materials">{t('graph.view_materials')}</option>
            <option value="properties">{t('graph.view_properties')}</option>
            <option value="synthesis">{t('graph.view_synthesis')}</option>
          </select>

          <div className="type-filters">
            {Object.keys(loadStatus?.nodeTypes ?? {}).map((type) => (
              <label key={type} className="type-filter">
                <input
                  type="checkbox"
                  checked={enabledTypes.has(type)}
                  onChange={(e) => {
                    const next = new Set(enabledTypes);
                    if (e.target.checked) next.add(type);
                    else next.delete(type);
                    setEnabledTypes(next);
                  }}
                />
                <span className="type-swatch" style={{ background: colors?.[type] ?? '#7f8c8d' }} />
                {t(`ontology.node_types.${type}`, { defaultValue: type })}
              </label>
            ))}
          </div>

          {edgeTypes.length > 0 && (
            <>
              <h3>{t('graph.edge_legend')}</h3>
              <ul className="edge-legend">
                {edgeTypes.map((type) => {
                  const style = EDGE_STYLES[type] ?? EDGE_STYLES.default;
                  return (
                    <li key={type}>
                      <span className={`edge-sample${style.dashed ? ' dashed' : ''}`} style={{ borderColor: style.color }} />
                      {t(`ontology.edge_types.${type}`, { defaultValue: type })}
                    </li>
                  );
                })}
              </ul>
            </>
          )}

          <h3>{t('graph.node_details')}</h3>
          {selected ? (
            <div className="node-details">
              <strong>{selected.name ?? selected.text ?? selected.id}</strong>
              <p>{selected.definition ?? '—'}</p>
              <div>{t('metrics.pagerank')}: {formatNumber(selected.pagerank ?? 0)}</div>
              <div>{t('metrics.betweenness')}: {formatNumber(selected.betweenness_centrality ?? 0)}</div>
            </div>
          ) : (
            <p className="hint">{t('graph.select_node_hint')}</p>
          )}
        </aside>

        <div className="graph-main">
          {loadStatus && (
            <div className={`status-banner ${loadStatus.nodeCount === 0 ? 'warn' : 'ok'}`}>
              {loadStatus.nodeCount === 0
                ? t('graph.empty_graph')
                : t('graph.loaded_from', { count: loadStatus.nodeCount, path: shortPath })}
              {loadStatus.warnings?.includes('missing_wow_files') && (
                <button type="button" className="btn-secondary btn-inline" onClick={() => api.runStage('metrics')}>
                  {t('graph.run_metrics')}
                </button>
              )}
            </div>
          )}
          <div className="graph-toolbar">
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t('graph.search_placeholder')}
              className="graph-search"
            />
            <span className="graph-stats">
              {t('graph.stats', { visible: stats.visible, total: stats.total, edges: stats.edges })}
            </span>
          </div>
          <div ref={containerRef} className="cy-container" />
        </div>
      </div>
    </div>
  );
}
