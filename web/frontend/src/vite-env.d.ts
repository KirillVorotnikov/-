/// <reference types="vite/client" />

declare module '*.json' {
  const value: Record<string, unknown>;
  export default value;
}

interface Window {
  cytoscape: (options: Record<string, unknown>) => {
    destroy: () => void;
    resize: () => void;
    fit: (eles?: unknown, padding?: number) => void;
    elements: () => unknown;
    nodes: () => { forEach: (fn: (n: unknown) => void) => void; filter: (fn: (n: unknown) => boolean) => unknown; length: number };
    edges: () => { forEach: (fn: (e: unknown) => void) => void; length: number };
    on: (event: string, selector: string, handler: (evt: { target: { data: (k: string) => unknown } }) => void) => void;
    animate: (props: unknown, opts: unknown) => void;
  };
  cytoscapeCoseBilkent: unknown;
}
