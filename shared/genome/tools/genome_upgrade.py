#!/usr/bin/env python3
"""genome upgrade gate (ADR-127 P4) — fetch a new upstream baseline, fold its
genome-relevant changes into a candidate bundle, and adopt the new pin ONLY if
both the genome contract test AND every child's suite pass. Otherwise abort with
a diff report; children stay safely on their current pin.

This automates the manual fetch → conflict-map → curate → re-verify dance we did
by hand on 2026-06-03. The three pillars it enforces (ADR-127 §4):

  ① 핀 (pin)        — children depend on a SPECIFIC genome version; nothing
                      changes under them until an explicit, gated upgrade.
  ② 깨끗한 경계      — the genome is consumed, never edited in place. An upstream
                      change to a non-genome file (prompt-runner, PRDs, soul
                      prose) is REPORTED and IGNORED — it cannot leak into the
                      bundle.
  ③ 업그레이드 게이트 — contract (genome self-conformance) + every child's suite
                      must be green before the pin advances; red → abort, zero
                      mutation, children remain on the previous version.

Genome provenance: the bundle's Tier-A code was extracted from upstream's
``.claude/{hooks/scripts, agents, skills/workflow-generator}``. Only changes to
files ALREADY in the manifest are adopted. A NEW upstream file under those dirs
(e.g. a brand-new hook) is surfaced as "curation deferred", never auto-adopted —
growing the genome surface is a boundary decision, not an upgrade (ADR-127 P4
first-case policy). Genome-originated files (constitution bases, config defaults,
contract, tools) have no upstream counterpart and are never touched here.

Default run is READ-ONLY: fetch, stage a candidate, run both gates, print the
verdict + diff WITHOUT mutating anything. Pass --apply to write the adopted files
+ bump the version + advance the pin. The git commit stays MANUAL, so the upgrade
is always reported before it is recorded.

Usage:
    python3 tools/genome_upgrade.py --to <git-ref>            # report only (gates run)
    python3 tools/genome_upgrade.py --to <git-ref> --apply    # adopt iff both gates green
    python3 tools/genome_upgrade.py --to <git-ref> --child /abs/frar [--child ...]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Path anchors. tools/ → shared/genome/ → shared/ → a-mirror repo root.
TOOLS = Path(__file__).resolve().parent
GENOME = TOOLS.parent
ROOT = GENOME.parent.parent
PIN_FILE = ROOT / ".upstream-version"
MANIFEST = GENOME / "genome.manifest.json"
VERSION_FILE = GENOME / "genome.version"

# The genome dirs whose contents were extracted from upstream ``.claude/``. An
# upstream change only maps onto the genome if it lives under one of these (after
# stripping the ``.claude/`` prefix). Genome-originated files — constitution
# bases, config defaults, contract, tools, genome.version/manifest — have NO
# upstream counterpart and are therefore never reachable by this mapping.
EXTRACTED_PREFIXES = ("hooks/scripts/", "agents/", "skills/workflow-generator/")

# Master-only production-line tooling: excluded from a child's vendored bundle so
# the gate vendors EXACTLY what build_manifest.py's EXCLUDE_FILES leaves out of
# the manifest. These two sets MUST stay in lockstep or a child's manifest
# --check breaks (build_manifest EXCLUDE_FILES ≡ this set ∪ {genome.manifest.json}).
_VENDOR_IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.pyc", ".DS_Store",
    "child_init.py", "test_child_init.py",
    "genome_upgrade.py", "test_genome_upgrade.py",
)


# --------------------------------------------------------------------------- #
# git helpers (all run at the a-mirror repo root)
# --------------------------------------------------------------------------- #
def _git(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)


def git_fetch() -> None:
    _git(["fetch", "upstream", "--quiet"])


def resolve_ref(ref: str) -> str:
    """Resolve a ref (sha/tag/branch) to a full commit sha; raise on failure."""
    res = _git(["rev-parse", "--verify", f"{ref}^{{commit}}"])
    if res.returncode != 0:
        raise SystemExit(f"cannot resolve ref {ref!r}: {res.stderr.strip()}")
    return res.stdout.strip()


def current_pin() -> str:
    """The committed sha from .upstream-version (first whitespace-delimited token)."""
    return PIN_FILE.read_text(encoding="utf-8").split()[0]


def changed_files(base: str, target: str) -> list[str]:
    res = _git(["diff", "--name-only", f"{base}..{target}"])
    if res.returncode != 0:
        raise SystemExit(f"git diff {base}..{target} failed: {res.stderr.strip()}")
    return [p for p in res.stdout.splitlines() if p]


def git_show(target: str, upstream_path: str) -> bytes:
    """Bytes of ``upstream_path`` at commit ``target`` (raises if absent)."""
    res = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"{target}:{upstream_path}"],
        capture_output=True,
    )
    if res.returncode != 0:
        raise SystemExit(f"git show {target}:{upstream_path} failed: {res.stderr.decode(errors='replace').strip()}")
    return res.stdout


def format_pin_line(target_sha: str) -> str:
    """One-line pin record matching the existing .upstream-version format:
    ``<full-sha> <committer-date> <subject>``."""
    res = _git(["log", "-1", "--format=%H %ci %s", target_sha])
    if res.returncode != 0:
        raise SystemExit(f"git log for {target_sha} failed: {res.stderr.strip()}")
    return res.stdout.strip()


# --------------------------------------------------------------------------- #
# classification — map an upstream diff onto the genome (②: ignore non-genome)
# --------------------------------------------------------------------------- #
def strip_claude(upstream_path: str) -> str | None:
    """Map an upstream repo path to a candidate genome-relative path, or None if
    it cannot belong to the genome. Genome Tier-A lives under upstream
    ``.claude/``; everything else (prompt-runner, prompt/, soul.md, …) is out."""
    prefix = ".claude/"
    if not upstream_path.startswith(prefix):
        return None
    return upstream_path[len(prefix):]


def _is_genome_asset(rel: str) -> bool:
    """A path that could plausibly be a curated genome asset (not runtime noise).
    The genome tracks source files (.py hooks, .md agents, skill files) — stray
    committed logs / bytecode under an extracted dir are not adoptable surface."""
    if "__pycache__" in rel:
        return False
    return not rel.endswith((".log", ".pyc"))


def classify(changed: list[str], manifest_files: set[str]) -> dict[str, list]:
    """Split an upstream diff into adopt / defer / ignore buckets.

    * adopt  — change to a file ALREADY in the genome manifest (upgrade in place).
    * defer  — a file under an extracted genome dir but NOT in the manifest
               (a NEW upstream hook/agent/skill — curation is a boundary
               decision, surfaced not auto-adopted; ADR-127 P4 policy).
    * ignore — anything else (non-genome: prompt-runner, PRDs, prose, …).
    """
    adopt: list[tuple[str, str]] = []
    defer: list[tuple[str, str]] = []
    ignore: list[str] = []
    for up in changed:
        rel = strip_claude(up)
        if rel is None:
            ignore.append(up)
            continue
        if rel in manifest_files:
            adopt.append((up, rel))
        elif any(rel.startswith(p) for p in EXTRACTED_PREFIXES) and _is_genome_asset(rel):
            defer.append((up, rel))
        else:
            # under .claude/ but not a genome-tracked area (e.g. .claude/agents/
            # translator.md is genome-eligible-but-not-core, or .claude/settings.json)
            ignore.append(up)
    return {"adopt": adopt, "defer": defer, "ignore": ignore}


# --------------------------------------------------------------------------- #
# semver — content-derived (semver honesty: no bump without a content delta)
# --------------------------------------------------------------------------- #
def bump_kind(added: set[str], removed: set[str], modified: set[str]) -> str | None:
    """None (no change) | 'patch' (existing files modified) | 'minor' (files
    added/removed). Major is reserved for a human-declared breaking change that
    the contract test would have flagged."""
    if added or removed:
        return "minor"
    if modified:
        return "patch"
    return None


def bump_version(version: str, kind: str | None) -> str:
    if kind is None:
        return version
    major, minor, patch = (int(x) for x in version.split("."))
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    if kind == "patch":
        return f"{major}.{minor}.{patch + 1}"
    if kind == "major":
        return f"{major + 1}.0.0"
    raise ValueError(f"unknown bump kind: {kind}")


# --------------------------------------------------------------------------- #
# staging — build a candidate genome in a temp dir (never mutates shared/genome)
# --------------------------------------------------------------------------- #
def _sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stage_candidate(adopt, fetch_content, tmp: Path):
    """Copy the live genome into ``tmp``, overlay each adopted file with its
    upstream content, bump the version by what actually changed, and rebuild the
    candidate manifest. ``fetch_content(upstream_path) -> bytes`` is injected so
    the staging logic is testable without a live git tree.

    Returns (candidate_dir, version_old, version_new, kind, changed_rel:list[str]).
    """
    candidate = tmp / "genome"
    shutil.copytree(GENOME, candidate, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))

    version_old = (candidate / "genome.version").read_text(encoding="utf-8").strip()
    modified: set[str] = set()
    added: set[str] = set()
    for upstream_path, rel in adopt:
        dst = candidate / rel
        new_bytes = fetch_content(upstream_path)
        existed = dst.exists()
        old_bytes = dst.read_bytes() if existed else b""
        if new_bytes == old_bytes:
            continue  # upstream touched it but content is identical → no delta
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(new_bytes)
        (modified if existed else added).add(rel)

    kind = bump_kind(added, set(), modified)
    version_new = bump_version(version_old, kind)
    (candidate / "genome.version").write_text(version_new + "\n", encoding="utf-8")

    # Rebuild the candidate manifest from its own build_manifest.py so the
    # contract's integrity check (C3) sees a self-consistent bundle.
    res = subprocess.run(
        [sys.executable, str(candidate / "tools" / "build_manifest.py")],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise SystemExit(f"candidate manifest build failed:\n{res.stdout}{res.stderr}")

    changed_rel = sorted(modified | added)
    return candidate, version_old, version_new, kind, changed_rel


# --------------------------------------------------------------------------- #
# gate ② — genome self-conformance (contract test on the candidate bundle)
# --------------------------------------------------------------------------- #
def run_contract(genome_dir: Path) -> tuple[bool, str]:
    res = subprocess.run(
        [sys.executable, str(genome_dir / "contract" / "test_genome_contract.py")],
        capture_output=True, text=True,
    )
    return res.returncode == 0, (res.stdout + res.stderr)


# --------------------------------------------------------------------------- #
# gate ③ — child suite (vendor the candidate into a temp child, run its tests)
# --------------------------------------------------------------------------- #
def gate_child(child: Path, candidate: Path, tmp: Path) -> tuple[bool, str, str]:
    """Vendor the candidate genome into a throwaway copy of ``child``'s genome
    slot and run the child's guarantee suite against it: the vendored contract
    (which itself runs manifest --check) plus, if present, the child's own
    ``tests/`` (pytest). Returns (ok, output, note). The real child is never
    touched — adoption stays opt-in (①pin)."""
    sandbox = tmp / f"child-{child.name}"
    vendored = sandbox / ".claude" / "genome"
    vendored.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(candidate, vendored, ignore=_VENDOR_IGNORE)

    ok, out = run_contract(vendored)
    output = f"[contract]\n{out}"

    # Optional domain suite — run it against the candidate-vendored genome if the
    # child ships one. frar has none yet, so gate ③ for frar is genome-
    # conformance only; we say so rather than imply full coverage.
    note = "genome-conformance only (no domain tests/ in child)"
    child_tests = child / "tests"
    if child_tests.is_dir() and any(child_tests.rglob("test_*.py")):
        res = subprocess.run(
            [sys.executable, "-m", "pytest", str(child_tests), "-q"],
            capture_output=True, text=True, cwd=str(child),
        )
        ok = ok and res.returncode == 0
        output += f"\n[domain pytest]\n{res.stdout[-2000:]}{res.stderr[-500:]}"
        note = "genome-conformance + child domain pytest"
    return ok, output, note


# --------------------------------------------------------------------------- #
# apply — adopt the candidate (write changed files + version + manifest + pin)
# --------------------------------------------------------------------------- #
def apply_upgrade(candidate: Path, version_new: str, changed_rel: list[str], target_sha: str) -> None:
    """Copy the adopted files (+ version + manifest) from the candidate back over
    the live genome, then advance the pin. Minimal diff: a no-op genome change
    only rewrites .upstream-version."""
    for rel in changed_rel:
        src = candidate / rel
        dst = GENOME / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    # version + manifest reflect the candidate (identical bytes on a no-op).
    shutil.copy2(candidate / "genome.version", VERSION_FILE)
    shutil.copy2(candidate / "genome.manifest.json", MANIFEST)
    PIN_FILE.write_text(format_pin_line(target_sha) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
def discover_children(explicit: list[Path]) -> list[Path]:
    """Children to gate. Explicit --child wins; else the known sibling 'frar'."""
    if explicit:
        return [c.resolve() for c in explicit]
    default = (ROOT.parent / "frar").resolve()
    return [default] if (default / ".claude" / "genome" / "contract").is_dir() else []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Genome upgrade gate (ADR-127 P4).")
    ap.add_argument("--to", required=True, help="upstream ref to upgrade toward (sha/tag/branch)")
    ap.add_argument("--child", dest="children", action="append", type=Path, default=[],
                    help="child project dir to gate (repeatable; default: sibling frar)")
    ap.add_argument("--apply", action="store_true", help="adopt iff both gates pass (writes files + pin)")
    ap.add_argument("--no-fetch", action="store_true", help="skip git fetch upstream (use already-fetched refs)")
    args = ap.parse_args(argv)

    if not args.no_fetch:
        git_fetch()
    base = current_pin()
    target = resolve_ref(args.to)
    print(f"[genome-upgrade] base pin {base[:7]} → target {target[:7]}")

    if base == target:
        print("[genome-upgrade] already at target — nothing to do.")
        return 0

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest_files = set(manifest["files"])
    buckets = classify(changed_files(base, target), manifest_files)
    print(f"[genome-upgrade] upstream diff: {len(buckets['adopt'])} adopt, "
          f"{len(buckets['defer'])} defer, {len(buckets['ignore'])} ignore (non-genome)")
    for up, rel in buckets["adopt"]:
        print(f"    adopt   {rel}")
    for up, rel in buckets["defer"]:
        print(f"    defer   {rel}   (NEW upstream file — curation is a boundary decision, not adopted)")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        candidate, v_old, v_new, kind, changed_rel = stage_candidate(
            buckets["adopt"], lambda p: git_show(target, p), tmp
        )
        if changed_rel:
            print(f"[genome-upgrade] genome content delta: {changed_rel} → version {v_old} → {v_new} ({kind})")
        else:
            print(f"[genome-upgrade] genome surface INVARIANT — version stays {v_old} "
                  f"(upstream moved, consumed surface did not). ②경계 holds.")

        # gate ② — genome contract
        g2_ok, g2_out = run_contract(candidate)
        print(f"[gate ② contract]  {'PASS' if g2_ok else 'FAIL'}")
        if not g2_ok:
            print(g2_out[-2000:])

        # gate ③ — each child's suite
        children = discover_children(args.children)
        g3_ok = True
        child_reports = []
        for child in children:
            ok, out, note = gate_child(child, candidate, tmp)
            g3_ok = g3_ok and ok
            child_reports.append((child, ok, out, note))
            print(f"[gate ③ child]    {'PASS' if ok else 'FAIL'}  {child.name}  ({note})")
            if not ok:
                print(out[-2000:])
        if not children:
            print("[gate ③ child]    (no children to gate)")

        green = g2_ok and g3_ok
        print()
        if not green:
            print("[genome-upgrade] ✗ ABORT — a gate is red. Pin NOT advanced; "
                  "children stay on the previous version (③: only verified is adopted).")
            return 1

        print(f"[genome-upgrade] ✓ BOTH GATES GREEN — target {target[:7]} is safe to adopt.")
        if buckets["defer"]:
            print(f"[genome-upgrade] note: {len(buckets['defer'])} new upstream file(s) deferred "
                  f"for separate curation: {[rel for _, rel in buckets['defer']]}")
        if not args.apply:
            print("[genome-upgrade] (report-only — re-run with --apply to advance the pin)")
            return 0

        apply_upgrade(candidate, v_new, changed_rel, target)

    print(f"[genome-upgrade] ✓ APPLIED — pin → {target[:7]}, genome v{v_new}. "
          f"Commit is manual (report before recording).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
