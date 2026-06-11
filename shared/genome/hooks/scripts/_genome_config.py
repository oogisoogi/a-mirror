#!/usr/bin/env python3
"""Genome config loader (Tier A) — exposes child-tunable knobs WITHOUT letting a
child edit genome code (②기둥: clean boundary).

Merge order (shallow per top-level key, child wins):
    shared/genome/genome.config.default.yaml   (genome defaults — this bundle)
    <project_root>/genome.config.yaml          (child override — optional)

PyYAML is the genome's single runtime dependency for config. If it is missing,
defaults-from-disk cannot be parsed: the loader returns built-in fallbacks and
warns on stderr (it never silently drops a child override).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

_GENOME = Path(__file__).resolve().parents[2]  # .../shared/genome/
_DEFAULTS_FILE = _GENOME / "genome.config.default.yaml"

# Built-in fallback used only if PyYAML is unavailable AND/OR files are missing.
# Mirrors genome.config.default.yaml's load-bearing keys so hooks never hard-fail.
_BUILTIN_FALLBACK: dict[str, Any] = {
    "translation": {"enabled": False},
    "secrets": {"extra_patterns": []},
    "quality_gates": {"pacs_min": 70},
    "destructive_guard": {"extra_allow": []},
}


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import yaml  # PyYAML
    except ImportError:
        print(
            f"[genome] PyYAML not installed — using built-in fallbacks, "
            f"skipping {path.name}",
            file=sys.stderr,
        )
        return {}
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for key, val in over.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], val)
        else:
            out[key] = val
    return out


def load_config(project_root: str | os.PathLike | None = None) -> dict:
    """Return the effective config (genome defaults <- child override)."""
    defaults = _read_yaml(_DEFAULTS_FILE) or _BUILTIN_FALLBACK
    cfg = _merge(_BUILTIN_FALLBACK, defaults)  # built-in guarantees all keys exist
    if project_root:
        child = Path(project_root) / "genome.config.yaml"
        cfg = _merge(cfg, _read_yaml(child))
    return cfg


def get(dotted: str, project_root=None, default=None) -> Any:
    """Dotted-path accessor, e.g. get('translation.enabled')."""
    node: Any = load_config(project_root)
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def translation_enabled(project_root=None) -> bool:
    return bool(get("translation.enabled", project_root, default=False))


if __name__ == "__main__":
    import json

    print(json.dumps(load_config(os.getcwd()), ensure_ascii=False, indent=2))
