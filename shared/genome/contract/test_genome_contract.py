#!/usr/bin/env python3
"""Genome contract test (P1 initial) — asserts the genome exposes the interfaces
that child workflows depend on.

Tier A code genome. The upgrade gate (P4) re-runs this against a fetched genome
version BEFORE adoption: a child only pins a new version if the contract passes
(plus the child's own suite). This is the genome's self-conformance suite, kept
dependency-free (stdlib only) so it runs anywhere a child runs.

Run: python3 contract/test_genome_contract.py   (exit 0 = pass, 1 = fail)
"""
from __future__ import annotations

import importlib.util
import json
import py_compile
import re
import subprocess
import sys
from pathlib import Path

GENOME = Path(__file__).resolve().parent.parent
HOOKS = GENOME / "hooks" / "scripts"

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))
        _failures.append(name)


# C1 — required bundle structure (what a child expects to consume)
check("genome.version present", (GENOME / "genome.version").exists())
check("genome.manifest.json present", (GENOME / "genome.manifest.json").exists())
check("hooks/scripts/ present", HOOKS.is_dir())
check(
    "core agents present (reviewer, fact-checker)",
    (GENOME / "agents" / "reviewer.md").exists()
    and (GENOME / "agents" / "fact-checker.md").exists(),
)
check("core skill present (workflow-generator)", (GENOME / "skills" / "workflow-generator").is_dir())

# C2 — genome.version is semver
version = (GENOME / "genome.version").read_text(encoding="utf-8").strip()
check("genome.version is semver", bool(re.fullmatch(r"\d+\.\d+\.\d+", version)), version)

# C3 — manifest integrity (bundle matches its recorded hashes)
result = subprocess.run(
    [sys.executable, str(GENOME / "tools" / "build_manifest.py"), "--check"],
    capture_output=True,
    text=True,
)
check("manifest integrity (build_manifest --check)", result.returncode == 0, result.stdout.strip())

# C4 — every hook compiles (syntax-clean; no import side effects executed)
hook_files = sorted(HOOKS.glob("*.py"))
check("hooks present (>= 20)", len(hook_files) >= 20, f"found {len(hook_files)}")
for path in hook_files:
    try:
        py_compile.compile(str(path), doraise=True)
        compiled = True
        err = ""
    except py_compile.PyCompileError as exc:  # noqa: PERF203
        compiled = False
        err = str(exc).splitlines()[-1] if str(exc) else "compile error"
    check(f"compiles: {path.name}", compiled, err)

# C5 — context-preservation core exposes a public API (child hooks depend on it)
ctx_lib = HOOKS / "_context_lib.py"
check("_context_lib.py present", ctx_lib.exists())
if ctx_lib.exists():
    src = ctx_lib.read_text(encoding="utf-8")
    check("_context_lib defines public functions", bool(re.search(r"^def [a-z]\w+\(", src, re.M)))

# C6 — manifest lists the version-pinned files (children pin against this set)
manifest_path = GENOME / "genome.manifest.json"
if manifest_path.exists():
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    check("manifest records genome_version", manifest.get("genome_version") == version)
    check("manifest tracks hook scripts", any(p.startswith("hooks/scripts/") for p in manifest.get("files", {})))


# C7 — extension points (P2): genome.config defaults + loader functional
check("genome.config.default.yaml present", (GENOME / "genome.config.default.yaml").exists())
loader = HOOKS / "_genome_config.py"
check("_genome_config.py present", loader.exists())
if loader.exists():
    spec = importlib.util.spec_from_file_location("_genome_config", loader)
    try:
        gc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gc)
        check("_genome_config loads + translation default off", gc.translation_enabled() is False)
    except Exception as exc:  # noqa: BLE001
        check("_genome_config loads", False, f"{type(exc).__name__}: {exc}")

# C8 — constitution base (P2): @import targets present for child overlay model
for base_file in ("soul.base.md", "CLAUDE.base.md", "AGENTS.base.md"):
    check(f"constitution base: {base_file}", (GENOME / base_file).exists())

# C9 — hooks consumption template (P2): standard wiring the scaffolder injects
check("settings.hooks.template.json present", (GENOME / "settings.hooks.template.json").exists())

print()
if _failures:
    print(f"CONTRACT FAIL — {len(_failures)} violation(s): {_failures}")
    sys.exit(1)
print(f"CONTRACT PASS — genome v{version} conforms ({len(hook_files)} hooks verified)")
sys.exit(0)
