#!/usr/bin/env python3
"""Tests for the P4 upgrade gate (ADR-127). Stdlib-only, no network/git needed:
the staging logic takes an injected ``fetch_content`` so the gates run against a
real copy of the live genome deterministically.

Run: python3 tools/test_genome_upgrade.py   (exit 0 = pass, 1 = fail)
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "genome_upgrade", Path(__file__).resolve().parent / "genome_upgrade.py"
)
gu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gu)

_fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if not ok and detail else ""))
    if not ok:
        _fails.append(name)


# --------------------------------------------------------------------------- #
# classify — the ②경계 boundary: only manifest files adopt; new genome files
# defer; everything else (incl. the real first-case prompt-runner churn) ignores.
# --------------------------------------------------------------------------- #
def test_classify():
    manifest = {"hooks/scripts/context_guard.py", "agents/reviewer.md",
                "skills/workflow-generator/SKILL.md"}
    changed = [
        ".claude/hooks/scripts/context_guard.py",      # adopt (in manifest)
        ".claude/hooks/scripts/retry_manager.py",      # defer (new hook — first case!)
        ".claude/agents/translator.md",                # defer (genome-eligible non-core)
        "prompt-runner/run.py",                        # ignore (non-genome)
        "soul.md",                                     # ignore (genome uses soul.base.md)
        "translations/glossary.yaml",                  # ignore
    ]
    b = gu.classify(changed, manifest)
    adopt = {rel for _, rel in b["adopt"]}
    defer = {rel for _, rel in b["defer"]}
    check("classify adopts in-manifest hook", adopt == {"hooks/scripts/context_guard.py"}, str(adopt))
    check("classify defers new hook (retry_manager) + non-core agent",
          defer == {"hooks/scripts/retry_manager.py", "agents/translator.md"}, str(defer))
    check("classify ignores all non-genome churn", len(b["ignore"]) == 3, str(b["ignore"]))


def test_classify_filters_runtime_noise():
    # a stray committed log / bytecode under an extracted dir is noise, not a
    # deferrable genome asset (the real first case had setup.init.log).
    manifest = {"hooks/scripts/context_guard.py"}
    changed = [".claude/hooks/scripts/.claude/hooks/setup.init.log",
               ".claude/hooks/scripts/__pycache__/x.pyc"]
    b = gu.classify(changed, manifest)
    check("runtime noise → ignore (not defer)", not b["defer"] and len(b["ignore"]) == 2, str(b))


def test_strip_claude():
    check("strip_claude maps .claude/ path", gu.strip_claude(".claude/hooks/scripts/x.py") == "hooks/scripts/x.py")
    check("strip_claude rejects non-.claude path", gu.strip_claude("prompt-runner/run.py") is None)


# --------------------------------------------------------------------------- #
# semver — content-derived, honest (no delta → no bump)
# --------------------------------------------------------------------------- #
def test_bump():
    check("no delta → no bump", gu.bump_kind(set(), set(), set()) is None)
    check("modified → patch", gu.bump_kind(set(), set(), {"a"}) == "patch")
    check("added → minor", gu.bump_kind({"a"}, set(), set()) == "minor")
    check("bump patch", gu.bump_version("0.1.0", "patch") == "0.1.1")
    check("bump minor", gu.bump_version("0.1.0", "minor") == "0.2.0")
    check("bump none keeps version", gu.bump_version("0.1.0", None) == "0.1.0")


# --------------------------------------------------------------------------- #
# staging + gate ② — no-op upgrade: empty adopt → genome invariant, contract green
# --------------------------------------------------------------------------- #
def test_stage_noop_contract_green():
    with tempfile.TemporaryDirectory() as td:
        cand, v_old, v_new, kind, changed = gu.stage_candidate([], lambda p: b"", Path(td))
        check("no-op keeps version", v_old == v_new and kind is None, f"{v_old}->{v_new} {kind}")
        check("no-op has no changed files", changed == [], str(changed))
        ok, out = gu.run_contract(cand)
        check("gate ② contract PASS on invariant candidate", ok, out[-400:])


# --------------------------------------------------------------------------- #
# staging + gate ② — modified existing hook → patch bump, manifest rebuilt, green
# --------------------------------------------------------------------------- #
def test_stage_modified_patch_bump_contract_green():
    # pick a real genome hook and adopt a benign, still-compiling variant of it.
    target_rel = "hooks/scripts/block_test_file_edit.py"
    original = (gu.GENOME / target_rel).read_bytes()
    fake = original + b"\n# adopted-by-upgrade-gate-test\n"
    adopt = [(f".claude/{target_rel}", target_rel)]
    with tempfile.TemporaryDirectory() as td:
        cand, v_old, v_new, kind, changed = gu.stage_candidate(adopt, lambda p: fake, Path(td))
        check("modified hook → patch bump", kind == "patch" and v_new == gu.bump_version(v_old, "patch"),
              f"{v_old}->{v_new} {kind}")
        check("changed list records the adopted file", changed == [target_rel], str(changed))
        # manifest reflects the new content + version
        man = json.loads((cand / "genome.manifest.json").read_text())
        check("candidate manifest records bumped version", man["genome_version"] == v_new, man["genome_version"])
        check("candidate manifest hash updated for adopted file",
              man["files"][target_rel] != _sha(original), "hash unchanged")
        ok, out = gu.run_contract(cand)
        check("gate ② contract PASS on modified candidate", ok, out[-400:])


# --------------------------------------------------------------------------- #
# gate ③ — vendor candidate into a throwaway child, run its contract
# --------------------------------------------------------------------------- #
def test_gate_child_against_candidate():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cand, *_ = gu.stage_candidate([], lambda p: b"", tmp)
        # synthesize a minimal child whose genome slot the gate will fill
        child = tmp / "fake-child"
        child.mkdir()
        ok, out, note = gu.gate_child(child, cand, tmp)
        check("gate ③ child contract PASS on vendored candidate", ok, out[-400:])
        check("gate ③ reports honest coverage note", "genome-conformance" in note, note)


# --------------------------------------------------------------------------- #
# gate ② ACTUALLY BLOCKS — a broken adopted hook must fail the contract (③: only
# verified is adopted). This is the load-bearing guarantee, so prove it red.
# --------------------------------------------------------------------------- #
def test_gate_blocks_broken_candidate():
    target_rel = "hooks/scripts/block_test_file_edit.py"
    broken = b"def (this is not valid python\n"
    adopt = [(f".claude/{target_rel}", target_rel)]
    with tempfile.TemporaryDirectory() as td:
        cand, *_ = gu.stage_candidate(adopt, lambda p: broken, Path(td))
        ok, out = gu.run_contract(cand)
        check("gate ② FAILS on a broken adopted hook (gate blocks)", not ok,
              "contract unexpectedly passed a non-compiling hook")


def _sha(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


if __name__ == "__main__":
    for fn in (test_strip_claude, test_classify, test_classify_filters_runtime_noise, test_bump,
               test_stage_noop_contract_green, test_stage_modified_patch_bump_contract_green,
               test_gate_child_against_candidate, test_gate_blocks_broken_candidate):
        print(f"\n{fn.__name__}:")
        fn()
    print()
    if _fails:
        print(f"UPGRADE-GATE TESTS FAIL — {len(_fails)}: {_fails}")
        raise SystemExit(1)
    print("UPGRADE-GATE TESTS PASS")
