"""
Pipeline launcher for the K2-18 web application.

Runs a pipeline stage with runtime config from web/runtime/config.toml
without modifying any files in src/.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = WEB_DIR.parent
RUNTIME_CONFIG = WEB_DIR / "runtime" / "config.toml"

STAGE_MODULES = {
    "slicer": "src.slicer",
    "concepts": "src.itext2kg_concepts",
    "graph": "src.itext2kg_graph",
    "dedup": "src.dedup",
    "refiner": "src.refiner_longrange",
    "metrics": "viz.graph2metrics",
    "fix": "viz.graph_fix",      
    "split": "viz.graph_split",      
    "graph2html": "viz.graph2html",
    "graph2viewer": "viz.graph2viewer",
}


def _patch_config_loader() -> None:
    """Redirect load_config() to runtime config when available."""
    if not RUNTIME_CONFIG.exists():
        return

    sys.path.insert(0, str(PROJECT_ROOT))
    import src.utils.config as config_module

    original_load = config_module.load_config

    def patched_load(config_path=None):
        return original_load(RUNTIME_CONFIG)

    config_module.load_config = patched_load


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python web/run_with_config.py <stage>", file=sys.stderr)
        print(f"Stages: {', '.join(STAGE_MODULES)}", file=sys.stderr)
        return 1

    stage = sys.argv[1].lower()
    module_name = STAGE_MODULES.get(stage)
    if module_name is None:
        print(f"Unknown stage: {stage}", file=sys.stderr)
        return 1

    sys.path.insert(0, str(PROJECT_ROOT))
    _patch_config_loader()

    saved_argv = sys.argv[:]
    # runpy with alter_sys keeps extra launcher args (e.g. "slicer") in sys.argv,
    # which breaks argparse in stage modules that accept no positional arguments
    sys.argv = [saved_argv[0]]

    try:
        runpy.run_module(module_name, run_name="__main__", alter_sys=True)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        return 1
    finally:
        sys.argv = saved_argv
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
