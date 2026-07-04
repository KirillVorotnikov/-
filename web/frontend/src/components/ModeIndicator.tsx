import { useTranslation } from 'react-i18next';
import { formatModeSummary, type ConfigStatus } from '../utils/modeLabel';

interface Props {
  config: ConfigStatus;
}

export function ModeIndicator({ config }: Props) {
  const { modeTitle, providerLabel, modelLabel } = formatModeSummary(config);
  const isOffline = config.mode === 'offline';

  return (
    <div className="mode-indicator">
      <span className={`mode-dot ${isOffline ? 'offline' : 'online'}`} />
      <div className="mode-text">
        <span className="mode-title">{modeTitle}</span>
        <span className="mode-detail">{providerLabel}</span>
        <span className="mode-model">{modelLabel}</span>
      </div>
    </div>
  );
}

export function ModeKpiCard({ config }: { config: ConfigStatus }) {
  const { t } = useTranslation();
  const { modeTitle, providerLabel, modelLabel } = formatModeSummary(config);

  return (
    <div className="kpi-card">
      <small>{t('dashboard.kpi_mode')}</small>
      <strong>{modeTitle}</strong>
      <small>{providerLabel}</small>
      <small className="mode-model-kpi">{modelLabel}</small>
    </div>
  );
}
