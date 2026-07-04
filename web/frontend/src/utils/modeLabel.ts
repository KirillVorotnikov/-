/**
 * Human-readable labels for operational mode and provider.
 */

const PROVIDER_LABELS: Record<string, string> = {
  openrouter: 'OpenRouter API',
  local_transformers: 'Локальная PyTorch-модель',
  ollama: 'Ollama',
  vllm: 'vLLM',
  local: 'Локальный HTTP',
};

export interface ConfigStatus {
  mode?: string;
  activeProvider?: string;
  sourceProvider?: string;
  modelName?: string | null;
  localModelPath?: string | null;
}

function basename(p: string | null | undefined): string {
  if (!p) return '';
  return p.split(/[/\\]/).pop() ?? p;
}

export function formatProviderLabel(provider: string | undefined): string {
  if (!provider) return '—';
  return PROVIDER_LABELS[provider] ?? provider;
}

export function formatModeSummary(config: ConfigStatus): {
  modeTitle: string;
  providerLabel: string;
  modelLabel: string;
} {
  const isOffline = config.mode === 'offline';
  const modeTitle = isOffline ? 'Оффлайн-режим' : 'Онлайн-режим';
  const providerLabel = formatProviderLabel(String(config.activeProvider ?? ''));

  let modelLabel = '—';
  if (isOffline) {
    modelLabel = basename(config.localModelPath) || 'локальная модель';
  } else {
    modelLabel = config.modelName ? String(config.modelName) : providerLabel;
  }

  return { modeTitle, providerLabel, modelLabel };
}
