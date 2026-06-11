#!/usr/bin/env python3
"""Genome manifest builder — deterministic SHA-256 inventory of the genome bundle.

Tier A code genome. Children pin a genome version; the upgrade gate (P4) calls
``--check`` to verify a fetched bundle matches its manifest before adoption.

Usage:
    python3 tools/build_manifest.py          # (re)generate genome.manifest.json
    python3 tools/build_manifest.py --check  # verify files match manifest (exit 1 on mismatch)

The manifest is intentionally free of timestamps/random data so the same bundle
always produces the same manifest (required for integrity comparison).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

GENOME = Path(__file__).resolve().parent.parent  # shared/genome/
MANIFEST = GENOME / "genome.manifest.json"
VERSION_FILE = GENOME / "genome.version"
# The scaffolder + its test are master-only production-line tooling (ADR-127 P3):
# they operate ON the genome but are NOT part of the consumed genome surface a
# child pins, so they are excluded from the integrity inventory (and from child
# vendoring). This keeps the genome version/manifest unchanged by adding tooling.
EXCLUDE_FILES = {
    "genome.manifest.json",
    "tools/child_init.py",
    "tools/test_child_init.py",
}
EXCLUDE_DIRS = {"__pycache__"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inventory() -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(GENOME.rglob("*")):
        if not path.is_file():
            continue
        if any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        rel = path.relative_to(GENOME).as_posix()
        if rel in EXCLUDE_FILES:
            continue
        files[rel] = _sha256(path)
    return files


def _version() -> str:
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def build() -> None:
    manifest = {"genome_version": _version(), "files": _inventory()}
    MANIFEST.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"manifest written: {len(manifest['files'])} files @ v{manifest['genome_version']}")


def check() -> int:
    if not MANIFEST.exists():
        print("FAIL: genome.manifest.json missing")
        return 1
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    current = _inventory()
    ok = True
    for rel, digest in manifest["files"].items():
        if current.get(rel) != digest:
            print(f"MISMATCH: {rel}")
            ok = False
    for rel in current:
        if rel not in manifest["files"]:
            print(f"UNTRACKED: {rel}")
            ok = False
    if manifest["genome_version"] != _version():
        print(f"VERSION DRIFT: manifest {manifest['genome_version']} != {_version()}")
        ok = False
    print("OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(check() if "--check" in sys.argv[1:] else (build() or 0))
