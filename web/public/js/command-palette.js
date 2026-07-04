/**
 * Command palette for quick navigation and operations.
 */

export class CommandPalette {
  constructor(dialog, input, resultsList, handlers) {
    this.dialog = dialog;
    this.input = input;
    this.resultsList = resultsList;
    this.handlers = handlers;
    this.commands = [
      { id: 'view-dashboard', label: 'Go to Dashboard', group: 'Navigation', action: () => handlers.showView('dashboard') },
      { id: 'view-pipeline', label: 'Go to Pipeline', group: 'Navigation', action: () => handlers.showView('pipeline') },
      { id: 'view-graph', label: 'Go to Graph Explorer', group: 'Navigation', action: () => handlers.showView('graph') },
      { id: 'view-hypotheses', label: 'Go to KPI Hypotheses', group: 'Navigation', action: () => handlers.showView('hypotheses') },
      { id: 'run-pipeline', label: 'Run Full Pipeline', group: 'Actions', action: () => handlers.runPipeline() },
      { id: 'generate-hypotheses', label: 'Generate KPI Hypotheses', group: 'Actions', action: () => handlers.generateHypotheses() },
      { id: 'toggle-theme', label: 'Toggle Light/Dark Theme', group: 'Actions', action: () => handlers.toggleTheme() },
      { id: 'mode-online', label: 'Switch to Online Mode', group: 'Actions', action: () => handlers.setMode('online') },
      { id: 'mode-offline', label: 'Switch to Offline Mode', group: 'Actions', action: () => handlers.setMode('offline') },
    ];
    this.selectedIndex = 0;
    this.dynamicItems = [];

    input.addEventListener('input', () => this.render());
    input.addEventListener('keydown', (event) => this.onKeyDown(event));
    resultsList.addEventListener('click', (event) => {
      const item = event.target.closest('li[data-index]');
      if (item) this.execute(Number(item.dataset.index));
    });
  }

  open() {
    this.input.value = '';
    this.selectedIndex = 0;
    this.render();
    this.dialog.showModal();
    this.input.focus();
  }

  close() {
    this.dialog.close();
  }

  setSearchItems(items) {
    this.dynamicItems = items;
  }

  allItems() {
    const query = this.input.value.trim().toLowerCase();
    const staticMatches = this.commands.filter((cmd) =>
      cmd.label.toLowerCase().includes(query) || cmd.group.toLowerCase().includes(query),
    );
    const dynamicMatches = this.dynamicItems.filter((item) =>
      item.label.toLowerCase().includes(query),
    );
    return [...staticMatches, ...dynamicMatches];
  }

  render() {
    const items = this.allItems();
    this.selectedIndex = Math.min(this.selectedIndex, Math.max(0, items.length - 1));
    this.resultsList.innerHTML = items
      .map(
        (item, index) =>
          `<li data-index="${index}" class="${index === this.selectedIndex ? 'selected' : ''}">` +
          `<strong>${item.label}</strong>` +
          `<br><small>${item.group ?? 'Concept'}</small></li>`,
      )
      .join('');
  }

  onKeyDown(event) {
    const items = this.allItems();
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      this.selectedIndex = Math.min(this.selectedIndex + 1, items.length - 1);
      this.render();
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      this.selectedIndex = Math.max(this.selectedIndex - 1, 0);
      this.render();
    } else if (event.key === 'Enter') {
      event.preventDefault();
      this.execute(this.selectedIndex);
    } else if (event.key === 'Escape') {
      this.close();
    }
  }

  execute(index) {
    const items = this.allItems();
    const item = items[index];
    if (!item) return;
    this.close();
    if (item.action) {
      item.action();
    } else if (item.nodeId) {
      this.handlers.focusNode(item.nodeId);
    }
  }
}
