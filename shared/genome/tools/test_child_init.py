#!/usr/bin/env python3
"""Scaffolder contract test (ADR-127 P3) — asserts a child born by child_init.py
satisfies the three safety pillars STRUCTURALLY.

Master-only production-line test (excluded from the genome manifest + child
vendoring, like child_init.py itself). Births a throwaway child into a temp dir
from a synthetic domain source, then verifies ①핀 ②깨끗한 경계 ③게이트.

Run: python3 tools/test_child_init.py   (exit 0 = pass, 1 = fail)
"""
from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
GENOME = TOOLS.parent  # shared/genome/

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if not ok and detail else ""))
    if not ok:
        _failures.append(name)


def _load_child_init():
    spec = importlib.util.spec_from_file_location("child_init", TOOLS / "child_init.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    ci = _load_child_init()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # synthetic domain source (Tier C content to migrate)
        src = root / "src_domain"
        (src / "docs").mkdir(parents=True)
        (src / "docs" / "DECISIONS.md").write_text("# decisions\n", encoding="utf-8")
        (src / "code").mkdir()
        (src / "code" / "main.py").write_text("print('domain')\n", encoding="utf-8")
        (src / "README.md").write_text("# domain readme\n", encoding="utf-8")
        (src / "CLAUDE.md").write_text("# old charter\n\n## 도메인 원칙\n- 로컬 전용\n", encoding="utf-8")
        (src / ".gitignore").write_text("domain_artifacts/\n", encoding="utf-8")

        child = root / "born_child"
        rc = ci.main([
            "--name", "tchild",
            "--domain", "test-domain",
            "--target", str(child),
            "--from", str(src),
            "--claude-overlay-from", str(src / "CLAUDE.md"),
            "--set", "translation.enabled=false",
            "--local-data-dir", "data",
        ])
        check("child_init.main() returns 0 (clean birth)", rc == 0, f"rc={rc}")

        g = child / ".claude" / "genome"

        # ① pin — version marker + isolated genome bundle + integrity
        check("① .genome-version pin marker at child root", (child / ".genome-version").exists())
        check("① genome isolated in .claude/genome/", (g / "genome.version").exists())
        check("① genome.manifest.json vendored", (g / "genome.manifest.json").exists())
        res = subprocess.run([sys.executable, str(g / "tools" / "build_manifest.py"), "--check"],
                             capture_output=True, text=True)
        check("① manifest --check passes in child", res.returncode == 0, res.stdout.strip())

        # ③ gate — the vendored contract (P4 entry point) passes
        res = subprocess.run([sys.executable, str(g / "contract" / "test_genome_contract.py")],
                             capture_output=True, text=True)
        check("③ vendored contract test passes", res.returncode == 0, res.stdout.strip()[-200:])

        # ② clean boundary — genome code byte-identical to master (not edited in place)
        sample = "hooks/scripts/_genome_config.py"
        check("② vendored hook byte-identical to master genome",
              _sha(g / sample) == _sha(GENOME / sample))

        # ② overlay — CLAUDE/AGENTS/soul @import the base, not in-place copies
        for fname, base in (("CLAUDE.md", "CLAUDE.base.md"),
                            ("AGENTS.md", "AGENTS.base.md"),
                            ("soul.md", "soul.base.md")):
            txt = (child / fname).read_text(encoding="utf-8")
            check(f"② {fname} @imports genome base", f"@.claude/genome/{base}" in txt)
        check("② CLAUDE overlay carried migrated charter body",
              "로컬 전용" in (child / "CLAUDE.md").read_text(encoding="utf-8"))

        # ② config override merges over genome defaults
        probe = (
            "import sys; sys.path.insert(0, r'%s'); import _genome_config as gc; "
            "c=gc.load_config(r'%s'); "
            "print('T' if c['translation']['enabled'] is False else 'F'); "
            "print('G' if c['quality_gates']['pacs_min']==70 else 'B')"
            % (g / "hooks" / "scripts", child)
        )
        res = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
        out = res.stdout.split()
        check("② config: child override translation=off loads", out[:1] == ["T"], res.stderr[-200:])
        check("② config: genome default inherited (pacs_min=70)", out[1:2] == ["G"])

        # active mirrors — Claude Code auto-load paths
        check("active mirror: .claude/agents/reviewer.md", (child / ".claude/agents/reviewer.md").exists())
        check("active mirror: .claude/agents/fact-checker.md", (child / ".claude/agents/fact-checker.md").exists())
        check("active mirror: .claude/skills/workflow-generator/", (child / ".claude/skills/workflow-generator").is_dir())

        # hooks wired to the vendored genome path
        settings = (child / ".claude" / "settings.json").read_text(encoding="utf-8")
        check("settings.json wired to .claude/genome/hooks/scripts/",
              ".claude/genome/hooks/scripts/" in settings
              and ".claude/hooks/scripts/" not in settings.replace(".claude/genome/hooks/scripts/", ""))

        # Tier C migration — domain content present, runtime/charter handled specially
        check("Tier C migrated: docs/", (child / "docs" / "DECISIONS.md").exists())
        check("Tier C migrated: code/", (child / "code" / "main.py").exists())
        check("Tier C migrated: README.md", (child / "README.md").exists())
        check("source CLAUDE.md NOT copied verbatim (became overlay)",
              "# old charter" not in (child / "CLAUDE.md").read_text(encoding="utf-8"))

        # local-only data gitignore (biometric data stays off git + gdrive)
        gi = (child / ".gitignore").read_text(encoding="utf-8")
        check("local-only data/ gitignored", "data/" in gi and "*.key" in gi)
        check("migrated domain .gitignore merged", "domain_artifacts/" in gi)

        # scaffolder NOT shipped into the child (master-only tooling)
        check("scaffolder excluded from child bundle",
              not (g / "tools" / "child_init.py").exists()
              and (g / "tools" / "build_manifest.py").exists())

    print()
    if _failures:
        print(f"SCAFFOLDER CONTRACT FAIL — {len(_failures)} violation(s): {_failures}")
        return 1
    print("SCAFFOLDER CONTRACT PASS — child_init births a clean child (①핀 ②경계 ③게이트).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
