#!/usr/bin/env python3
"""child-init — genome scaffolder (ADR-127 P3).

Births a CLEAN child workflow from the pinned genome. A child born here satisfies
the three safety pillars *structurally* (not by anyone's care):

  ① 핀 (pin)            — the child vendors ONE specific genome version into
                          ``<child>/.claude/genome/`` (immutable, integrity-pinned).
  ② 깨끗한 경계 (clean)  — the child never edits genome code; it customizes only via
                          the constitution overlay (@import) + ``genome.config.yaml``.
  ③ 게이트 가능 (gate)   — the vendored contract + the child's own suite are the
                          entry point the P4 upgrade gate runs before re-pinning.

Layout decision (load-bearing — see ADR-127 §6):

  The genome root *inside a child* is ``<child>/.claude/genome/`` and mirrors the
  master's ``shared/genome/`` 1:1. This single isolation directory is the ONLY
  layout that keeps all three genome path-assumptions consistent at once:

    * ``build_manifest.py``  GENOME = tools/../  → ``.claude/genome``  (manifest
      ``--check`` is two-way strict: any UNTRACKED file fails, so the genome MUST
      live alone in its own dir — it cannot sit directly under ``.claude/``).
    * ``test_genome_contract.py``  GENOME = contract/../ → ``.claude/genome``.
    * ``_genome_config.py``  GENOME = scripts/../../ → ``.claude/genome``.

  Hooks are therefore invoked from ``.claude/genome/hooks/scripts/`` — the
  scaffolder rewrites the settings template's hook paths accordingly. Only the
  two things Claude Code auto-discovers by convention — agents and skills — are
  ALSO mirrored to the standard ``.claude/agents`` / ``.claude/skills`` so the
  child can actually use reviewer / fact-checker / workflow-generator. Those
  mirrors live outside ``.claude/genome`` so they never trip manifest --check;
  the integrity-authoritative copies remain pinned inside ``.claude/genome``.

Usage:
    python3 tools/child_init.py \
        --name frar --domain facial-recognition-attendance \
        --target /abs/path/to/frar \
        --from /abs/path/to/church_attendance \
        --claude-overlay-from /abs/path/to/church_attendance/CLAUDE.md \
        --set translation.enabled=false \
        --local-data-dir data

Re-running into a non-empty target requires --force (genome dirs are refreshed;
the child's own files are preserved unless they collide with a generated file).
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

GENOME = Path(__file__).resolve().parent.parent  # .../shared/genome/
GENOME_VERSION = (GENOME / "genome.version").read_text(encoding="utf-8").strip()

# Files/dirs never copied when vendoring (runtime / build noise + master-only
# production-line tooling — children don't scaffold grandchildren, so the
# scaffolder itself and its test are not shipped into a child's pinned bundle).
_VENDOR_IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.pyc", ".DS_Store", "child_init.py", "test_child_init.py"
)

# Agents that are genome CORE (auto-injected into every child). Skills likewise.
# (Callable assets like translator / doctoral-writing are opt-in and NOT injected
#  here — the child opts in via genome.config.yaml; frar stays Korean-only.)
_CORE_AGENTS = ("reviewer.md", "fact-checker.md")
_CORE_SKILLS = ("workflow-generator",)


# --------------------------------------------------------------------------- #
# pillar ① — vendor the pinned genome bundle into <child>/.claude/genome
# --------------------------------------------------------------------------- #
def vendor_genome(child: Path) -> Path:
    """Mirror shared/genome/ → <child>/.claude/genome/ (byte-identical, integrity-pinned)."""
    dst = child / ".claude" / "genome"
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(GENOME, dst, ignore=_VENDOR_IGNORE)
    # Quick-lookup pin marker at the child root (the authoritative version still
    # lives in .claude/genome/genome.version; this is a convenience pointer).
    (child / ".genome-version").write_text(GENOME_VERSION + "\n", encoding="utf-8")
    return dst


def mirror_active_assets(child: Path) -> None:
    """Copy core agents/skills to the conventional .claude paths Claude Code loads."""
    g = child / ".claude" / "genome"
    agents_dst = child / ".claude" / "agents"
    skills_dst = child / ".claude" / "skills"
    agents_dst.mkdir(parents=True, exist_ok=True)
    skills_dst.mkdir(parents=True, exist_ok=True)
    for name in _CORE_AGENTS:
        shutil.copy2(g / "agents" / name, agents_dst / name)
    for name in _CORE_SKILLS:
        src = g / "skills" / name
        dest = skills_dst / name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest, ignore=_VENDOR_IGNORE)


# --------------------------------------------------------------------------- #
# hooks wiring — inject settings.json pointing at the vendored genome hooks
# --------------------------------------------------------------------------- #
def inject_settings(child: Path) -> None:
    """Write <child>/.claude/settings.json from the genome template, rewriting hook
    paths from the template's .claude/hooks/scripts/ to .claude/genome/hooks/scripts/."""
    template = (GENOME / "settings.hooks.template.json").read_text(encoding="utf-8")
    # Rewrite EVERY occurrence (commands AND the _genome doc string) so the child's
    # settings.json is fully consistent — hooks live under the vendored genome dir.
    wired = template.replace(".claude/hooks/scripts/", ".claude/genome/hooks/scripts/")
    (child / ".claude" / "settings.json").write_text(wired, encoding="utf-8")


# --------------------------------------------------------------------------- #
# pillar ② — constitution overlay (@import genome base + child-owned overlay)
# --------------------------------------------------------------------------- #
_IMPORT_NOTE = (
    "> 이 자식의 헌법 base는 게놈(Tier B)에서 `@import`로 상속한다. base를 제자리 수정하지 말 것\n"
    "> (게놈 버전드 — 업그레이드는 P4 게이트를 통한다). 도메인 특수화는 아래 overlay 섹션과\n"
    "> 루트 `genome.config.yaml`로만 한다(②기둥: 깨끗한 경계).\n"
)


def _overlay_body(overlay_from: Path | None, fallback_heading: str) -> str:
    """Overlay body placed under the @import. From a file (H1 demoted) or a skeleton."""
    if overlay_from and overlay_from.exists():
        text = overlay_from.read_text(encoding="utf-8").splitlines()
        # Drop a single leading H1 (the @import header already titles the file).
        out: list[str] = []
        dropped = False
        for line in text:
            if not dropped and line.startswith("# "):
                dropped = True
                continue
            out.append(line)
        return "\n".join(out).strip() + "\n"
    return (
        f"## {fallback_heading}\n\n"
        "> 이 자식의 도메인 목표·정체성·제약을 여기에 선언한다. (게놈 base가 방법론·안전·검증을\n"
        "> 제공하므로, overlay는 *도메인*에 집중한다.)\n\n"
        "- **도메인 목표**: (작성)\n"
        "- **도메인 제약**: (작성)\n"
    )


def write_constitution(child: Path, name: str, domain: str, claude_overlay_from: Path | None) -> None:
    specs = [
        ("CLAUDE.md", "CLAUDE.base.md", claude_overlay_from, f"{name} — 도메인 overlay (Tier C)"),
        ("AGENTS.md", "AGENTS.base.md", None, f"{name} — 에이전트 도메인 overlay"),
        ("soul.md", "soul.base.md", None, f"{name} — 정체성 overlay"),
    ]
    for fname, base, overlay_from, heading in specs:
        body = _overlay_body(overlay_from, heading)
        content = (
            f"# {name}\n\n"
            f"{_IMPORT_NOTE}\n"
            f"@.claude/genome/{base}\n\n"
            f"---\n\n"
            f"<!-- ⬇ 자식 소유 overlay (도메인: {domain}) — 게놈 업그레이드와 물리적으로 분리됨 -->\n\n"
            f"{body}"
        )
        (child / fname).write_text(content, encoding="utf-8")


# --------------------------------------------------------------------------- #
# pillar ② — genome.config.yaml (child override; defaults stay in the genome)
# --------------------------------------------------------------------------- #
def write_genome_config(child: Path, overrides: dict[str, str], domain: str) -> None:
    """Emit a minimal child override file. Only keys the child changes go here;
    everything else is inherited from the genome's genome.config.default.yaml."""
    lines = [
        f"# genome.config.yaml — {domain} child override (genome v{GENOME_VERSION})",
        "#",
        "# Shallow-merges OVER the genome's genome.config.default.yaml (child wins).",
        "# Never edit genome code to change behavior — tune it here (②기둥).",
        "",
    ]
    # Group dotted keys into nested YAML (one level deep is all the schema needs).
    tree: dict[str, dict[str, str]] = {}
    flat: dict[str, str] = {}
    for dotted, val in overrides.items():
        if "." in dotted:
            top, leaf = dotted.split(".", 1)
            tree.setdefault(top, {})[leaf] = val
        else:
            flat[dotted] = val
    for top, leaves in tree.items():
        lines.append(f"{top}:")
        for leaf, val in leaves.items():
            lines.append(f"  {leaf}: {val}")
    for k, val in flat.items():
        lines.append(f"{k}: {val}")
    (child / "genome.config.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# Tier C — migrate the child's domain content + local-data .gitignore
# --------------------------------------------------------------------------- #
# Source paths that are handled specially and must NOT be bulk-copied.
_MIGRATE_SKIP = {"CLAUDE.md", ".gitignore", ".git", "data", ".claude",
                 "genome.config.yaml", "AGENTS.md", "soul.md", ".genome-version"}


def migrate_domain(child: Path, source: Path) -> list[str]:
    """Copy the child's existing domain content (Tier C) into the new child.
    CLAUDE.md/.gitignore are handled elsewhere; runtime data is never migrated."""
    moved: list[str] = []
    for item in sorted(source.iterdir()):
        if item.name in _MIGRATE_SKIP:
            continue
        dst = child / item.name
        if item.is_dir():
            shutil.copytree(item, dst, ignore=_VENDOR_IGNORE, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dst)
        moved.append(item.name)
    return moved


_GENOME_GITIGNORE = [
    "# --- genome runtime (gitignored) ---",
    ".claude/context-snapshots/",
    ".claude/genome/**/__pycache__/",
    "__pycache__/",
    "*.pyc",
    "",
    "# --- OS ---",
    ".DS_Store",
    "Thumbs.db",
    "",
]


def write_gitignore(child: Path, source: Path | None, local_data_dir: str | None) -> None:
    """Compose .gitignore: genome runtime + migrated domain ignores + local-only
    biometric data exclusion (kept on-disk only; not committed, gdrive-sync off)."""
    lines = list(_GENOME_GITIGNORE)
    if local_data_dir:
        lines += [
            "# --- 로컬 전용 운영 데이터 (절대 커밋 금지 + gdrive 동기화 제외) ---",
            "# 생체정보(임베딩)·church.db·예배 영상·암호화 키는 이 기기에서만 관리한다.",
            f"{local_data_dir}/",
            "*.db",
            "*.db-journal",
            "*.key",
            "**/videos/",
            "**/photos/",
            "**/models/*.onnx",
            "",
        ]
    if source and (source / ".gitignore").exists():
        extra = (source / ".gitignore").read_text(encoding="utf-8")
        lines += ["# --- 이전된 도메인 .gitignore ---", extra.rstrip(), ""]
    # De-dup while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for ln in lines:
        key = ln.strip()
        if key and key in seen and not key.startswith("#"):
            continue
        if key and not key.startswith("#"):
            seen.add(key)
        deduped.append(ln)
    (child / ".gitignore").write_text("\n".join(deduped).rstrip() + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# pillar ③ + birth verification
# --------------------------------------------------------------------------- #
def verify_clean_birth(child: Path, expect_overrides: dict[str, str]) -> list[str]:
    """Assert the born child satisfies ①②③. Returns a list of failures (empty = clean)."""
    fails: list[str] = []
    g = child / ".claude" / "genome"

    # ① pin present + integrity (manifest --check on the vendored bundle)
    if not (g / "genome.version").exists():
        fails.append("pin: .claude/genome/genome.version missing")
    res = subprocess.run(
        [sys.executable, str(g / "tools" / "build_manifest.py"), "--check"],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        fails.append(f"pin integrity (manifest --check) FAILED:\n{res.stdout}{res.stderr}")

    # ③ vendored contract passes (the P4 gate's entry point)
    res = subprocess.run(
        [sys.executable, str(g / "contract" / "test_genome_contract.py")],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        fails.append(f"contract test FAILED:\n{res.stdout[-1500:]}{res.stderr[-500:]}")

    # ② clean boundary: constitution @imports the genome base (not an in-place copy)
    claude = (child / "CLAUDE.md").read_text(encoding="utf-8")
    if "@.claude/genome/CLAUDE.base.md" not in claude:
        fails.append("overlay: CLAUDE.md does not @import the genome base")

    # ② config override actually loads (child genome.config.yaml merges over defaults)
    probe = (
        "import sys; sys.path.insert(0, r'%s'); import _genome_config as gc; "
        "print(gc.load_config(r'%s'))" % (g / "hooks" / "scripts", child)
    )
    res = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    if res.returncode != 0:
        fails.append(f"config loader did not run:\n{res.stderr}")
    else:
        loaded = res.stdout
        # spot-check translation override if it was requested
        want = expect_overrides.get("translation.enabled")
        if want is not None:
            ok = ("'enabled': False" in loaded) if want == "false" else ("'enabled': True" in loaded)
            if not ok:
                fails.append(f"config override translation.enabled={want} not reflected: {loaded.strip()}")

    # active mirrors present (Claude Code auto-load)
    for a in _CORE_AGENTS:
        if not (child / ".claude" / "agents" / a).exists():
            fails.append(f"active mirror missing: .claude/agents/{a}")
    for s in _CORE_SKILLS:
        if not (child / ".claude" / "skills" / s).is_dir():
            fails.append(f"active mirror missing: .claude/skills/{s}")

    # settings wired to the vendored genome hooks
    settings = (child / ".claude" / "settings.json").read_text(encoding="utf-8")
    if ".claude/genome/hooks/scripts/" not in settings:
        fails.append("settings.json not wired to .claude/genome/hooks/scripts/")
    return fails


# --------------------------------------------------------------------------- #
def _parse_set(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in pairs:
        if "=" not in p:
            raise SystemExit(f"--set expects key=value, got: {p}")
        k, v = p.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Birth a clean child from the pinned genome (ADR-127 P3).")
    ap.add_argument("--name", required=True, help="child name (e.g. frar)")
    ap.add_argument("--domain", required=True, help="domain slug (e.g. facial-recognition-attendance)")
    ap.add_argument("--target", required=True, type=Path, help="child project dir (created)")
    ap.add_argument("--from", dest="source", type=Path, default=None, help="dir whose domain content (Tier C) to migrate")
    ap.add_argument("--claude-overlay-from", type=Path, default=None, help="file whose body becomes the CLAUDE.md overlay")
    ap.add_argument("--set", dest="overrides", action="append", default=[], help="genome.config override key=value (repeatable)")
    ap.add_argument("--local-data-dir", default=None, help="local-only data dir to gitignore (e.g. data)")
    ap.add_argument("--force", action="store_true", help="allow a non-empty target")
    args = ap.parse_args(argv)

    child: Path = args.target.resolve()
    if child.exists() and any(child.iterdir()) and not args.force:
        raise SystemExit(f"target {child} is non-empty; pass --force to proceed")
    child.mkdir(parents=True, exist_ok=True)
    overrides = _parse_set(args.overrides)

    print(f"[child-init] birthing '{args.name}' (domain={args.domain}) from genome v{GENOME_VERSION}")
    print(f"[child-init] target: {child}")

    vendor_genome(child)
    print("  ✓ ① pinned genome vendored → .claude/genome/")
    mirror_active_assets(child)
    print(f"  ✓ active mirrors → .claude/agents/{{{','.join(a[:-3] for a in _CORE_AGENTS)}}}, .claude/skills/{_CORE_SKILLS[0]}")
    inject_settings(child)
    print("  ✓ hooks wired → .claude/settings.json")
    write_constitution(child, args.name, args.domain, args.claude_overlay_from)
    print("  ✓ ② constitution overlay (@import base + child overlay)")
    write_genome_config(child, overrides, args.domain)
    print(f"  ✓ ② genome.config.yaml override: {overrides or '(none)'}")
    if args.source and args.source.exists():
        moved = migrate_domain(child, args.source.resolve())
        print(f"  ✓ Tier C migrated: {moved}")
    write_gitignore(child, args.source.resolve() if args.source else None, args.local_data_dir)
    print(f"  ✓ .gitignore (genome runtime + local-only data: {args.local_data_dir or '(none)'})")

    print("[child-init] verifying clean birth (①②③)…")
    fails = verify_clean_birth(child, overrides)
    if fails:
        print("\n[child-init] ✗ BIRTH NOT CLEAN:")
        for f in fails:
            print(f"  - {f}")
        return 1
    print(f"\n[child-init] ✓ CLEAN BIRTH — '{args.name}' pins genome v{GENOME_VERSION}, "
          f"satisfies ①핀 ②경계 ③게이트.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
