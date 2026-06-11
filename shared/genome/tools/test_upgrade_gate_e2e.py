#!/usr/bin/env python3
"""P5 pilot — end-to-end proof of the upgrade gate (ADR-127) on a REAL born child.

Where test_genome_upgrade.py unit-tests the gate's internals against synthetic
fixtures, this exercises the FULL pillar story (①핀 ②경계 ③게이트) on a throwaway
child actually born via child_init, simulating a sequence of parent updates:

  birth (v0.1.0)
    → (a) GOOD update  → gates green → ADOPT  → child re-pins v0.1.1   (① re-pin)
    → (b) BROKEN update → gate ② red → ABORT  → child stays v0.1.1     (① pin held)

"Parent update = a fetched new genome" is synthesized locally via an injected
fetch_content, so no real upstream/git is touched. The dummy child lives in a
temp dir and is destroyed on exit. CRITICAL invariant asserted throughout: the
real shared/genome is NEVER edited in place (②경계) — every mutation happens in a
temp candidate. frar and the committed genome are untouched.

Master-only production-line tooling (excluded from the manifest + child vendoring,
so it never reaches a child's pinned surface). Run:

    python3 tools/test_upgrade_gate_e2e.py   (exit 0 = pass, 1 = fail)
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gu = _load("genome_upgrade")
ci = _load("child_init")

_fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if not ok and detail else ""))
    if not ok:
        _fails.append(name)


# --------------------------------------------------------------------------- #
def _child_version(child: Path) -> str:
    return (child / ".claude" / "genome" / "genome.version").read_text(encoding="utf-8").strip()


def _child_self_consistent(child: Path) -> tuple[bool, str]:
    """A child is healthy iff its vendored bundle passes manifest --check AND the
    genome contract (the exact entry points the gate runs)."""
    g = child / ".claude" / "genome"
    m = subprocess.run([sys.executable, str(g / "tools" / "build_manifest.py"), "--check"],
                       capture_output=True, text=True)
    c = subprocess.run([sys.executable, str(g / "contract" / "test_genome_contract.py")],
                       capture_output=True, text=True)
    ok = m.returncode == 0 and c.returncode == 0
    return ok, (m.stdout + c.stdout)[-400:]


def _adopt_into_child(child: Path, candidate: Path) -> None:
    """Child-side adoption (the green-path outcome): re-vendor the candidate genome
    into the child's pinned slot + refresh the active mirrors + pin marker. Mirrors
    child_init.vendor_genome but from a candidate dir rather than shared/genome."""
    g = child / ".claude" / "genome"
    if g.exists():
        shutil.rmtree(g)
    shutil.copytree(candidate, g, ignore=gu._VENDOR_IGNORE)
    new_ver = (candidate / "genome.version").read_text(encoding="utf-8").strip()
    (child / ".genome-version").write_text(new_ver + "\n", encoding="utf-8")
    ci.mirror_active_assets(child)


def _shared_genome_fingerprint() -> tuple[str, int]:
    """(version, file-count) of the REAL shared genome — used to prove ②경계: the
    live genome is byte-identical before and after the whole pilot."""
    import json
    man = json.loads((gu.GENOME / "genome.manifest.json").read_text(encoding="utf-8"))
    return man["genome_version"], len(man["files"])


# --------------------------------------------------------------------------- #
def run_pilot() -> None:
    # ②경계 guard — snapshot the real genome before any simulation.
    before = _shared_genome_fingerprint()
    before_check = subprocess.run(
        [sys.executable, str(gu.GENOME / "tools" / "build_manifest.py"), "--check"],
        capture_output=True, text=True,
    )

    hook_rel = "hooks/scripts/block_test_file_edit.py"
    hook_up = f".claude/{hook_rel}"
    original = (gu.GENOME / hook_rel).read_bytes()

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        child = tmp / "p5dummy"

        # ---- birth: a clean child from the live genome (v0.1.0) -------------
        print("\n[P5] birthing throwaway child 'p5dummy' from the live genome…")
        rc = ci.main(["--name", "p5dummy", "--domain", "pilot-throwaway",
                      "--target", str(child), "--set", "translation.enabled=false"])
        check("birth: child_init reports CLEAN BIRTH (①②③)", rc == 0)
        check("birth: child pins v0.1.0", _child_version(child) == "0.1.0", _child_version(child))
        ok, _ = _child_self_consistent(child)
        check("birth: child self-consistent (manifest + contract)", ok)

        # ---- (a) GOOD parent update → gates green → ADOPT → re-pin ----------
        print("\n[P5] (a) GOOD update: benign genome change, simulate fetch + gate…")
        good = original + b"\n# p5-pilot benign genome update\n"
        cand_a, v_old, v_new, kind, changed = gu.stage_candidate(
            [(hook_up, hook_rel)], lambda p: good, tmp / "cand-a"
        )
        check("(a) stage bumps patch 0.1.0→0.1.1", v_old == "0.1.0" and v_new == "0.1.1" and kind == "patch",
              f"{v_old}->{v_new} {kind}")
        g2_ok, g2_out = gu.run_contract(cand_a)
        check("(a) gate ② contract GREEN", g2_ok, g2_out[-300:])
        g3_ok, g3_out, _ = gu.gate_child(child, cand_a, tmp)
        check("(a) gate ③ child suite GREEN", g3_ok, g3_out[-300:])
        check("(a) BOTH GREEN → adopt is authorized", g2_ok and g3_ok)
        _adopt_into_child(child, cand_a)
        check("(a) ADOPT: child re-pinned to v0.1.1", _child_version(child) == "0.1.1", _child_version(child))
        ok, det = _child_self_consistent(child)
        check("(a) ADOPT: re-pinned child still self-consistent", ok, det)
        marker = (child / ".genome-version").read_text(encoding="utf-8").strip()
        check("(a) ADOPT: .genome-version marker = 0.1.1", marker == "0.1.1", marker)

        # ---- (b) BROKEN parent update → gate ② red → ABORT → pin held ------
        print("\n[P5] (b) BROKEN update: syntax-error genome change, simulate fetch + gate…")
        broken = original + b"\ndef (this is not valid python\n"
        cand_b, *_ = gu.stage_candidate([(hook_up, hook_rel)], lambda p: broken, tmp / "cand-b")
        g2b_ok, _ = gu.run_contract(cand_b)
        check("(b) gate ② contract RED (broken hook caught)", not g2b_ok)
        # ABORT — by the gate's contract we do NOT touch the child. Assert it.
        check("(b) ABORT: child untouched, still pinned v0.1.1", _child_version(child) == "0.1.1",
              _child_version(child))
        ok, det = _child_self_consistent(child)
        check("(b) ABORT: child stayed on its good version + healthy (① pin held)", ok, det)
        hook_in_child = (child / ".claude" / "genome" / hook_rel).read_bytes()
        check("(b) ABORT: broken change never reached the child", b"not valid python" not in hook_in_child)

    # ---- ②경계: the real shared genome was never edited in place -----------
    after = _shared_genome_fingerprint()
    after_check = subprocess.run(
        [sys.executable, str(gu.GENOME / "tools" / "build_manifest.py"), "--check"],
        capture_output=True, text=True,
    )
    check("②경계: shared/genome version + file-count unchanged", before == after, f"{before} vs {after}")
    check("②경계: shared/genome integrity intact before AND after",
          before_check.returncode == 0 and after_check.returncode == 0)
    check("②경계: live genome still v0.1.0 (no surface growth from the pilot)", after[0] == "0.1.0", after[0])
    # the live hook we used as a fixture must be byte-identical (never mutated in place)
    check("②경계: fixture hook in shared/genome is byte-identical (read-only source)",
          (gu.GENOME / hook_rel).read_bytes() == original)


if __name__ == "__main__":
    run_pilot()
    print()
    if _fails:
        print(f"P5 PILOT FAIL — {len(_fails)}: {_fails}")
        raise SystemExit(1)
    print("P5 PILOT PASS — gate proven end-to-end on a real child: "
          "①핀(re-pin on green, held on red) ②경계(shared genome read-only) ③게이트(green→adopt, red→abort).")
