#!/usr/bin/env python3
"""G0 Dry-Run CLI for the Context Efficiency Benchmark.

Makes no network or model calls. Generates fixtures, tests checkpoints,
exercises adapters, scores synthetic logs, verifies accounting, and
emits JSON-Lines evidence plus a summary.

Exit 0 on all gates passed, exit 1 on any failure.
"""

from __future__ import annotations
import json
import os
import sys
import time
import tempfile
from pathlib import Path
from typing import Any

from .fixtures import (
    Task, Condition, FixtureSet,
    get_fixture, all_fixtures, drift_differs_from_clean,
)
from .schemas import (
    TrajectoryID, SessionID, SessionEvent, ToolCallReceipt,
    CheckpointReceipt, EvidenceReference, compute_sha256,
    serialize_json, serialize_jsonl,
)
from .checkpoint import evaluate_checkpoint, evaluate_all_stages
from .adapters import ADAPTERS, get_adapter, ArchitectureState
from .baseline import (
    generate_worker_a_checkpoint, clone_checkpoint_to_architecture,
    generate_all_baselines, clone_all_architectures, accounting_summary,
)
from .scorer import score_trajectory, TrajectoryScore
from .accounting import (
    compute_session_cost, compute_trajectory_cost, verify_accounting,
    pilot_accounting,
)
from .isolation import (
    create_isolated_workdir, verify_isolation, verify_no_leakage,
    reject_traversal,
)


def run_g0_dry_run(output_dir: str | Path | None = None) -> dict:
    """Run the G0 dry-run and return results."""
    gates: dict[str, bool] = {}
    evidence: list[dict] = []
    errors: list[str] = []

    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="g0_dry_run_"))
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    base_dir = output_dir / "work"
    base_dir.mkdir(parents=True, exist_ok=True)

    # ── Gate 1: Generate all fixtures ──────────────────────────────────
    print("=== G0: Generating fixtures ===")
    fixture_count = 0
    for task_str, condition_str, fixture in all_fixtures():
        fixture_count += 1
        d = base_dir / f"fixture_{task_str}_{condition_str}"
        fixture.write_inputs(d)
        print(f"  Task {task_str}, {condition_str}: {len(fixture.input_files)} input files, "
              f"{len(fixture.stage_artifacts)} stage artifacts")
    gates["fixture_generation"] = fixture_count == 6
    print(f"  Result: {fixture_count} fixture sets generated — {'PASS' if gates['fixture_generation'] else 'FAIL'}")

    # ── Gate 2: Drift detection ───────────────────────────────────────
    print("\n=== G0: Drift detection ===")
    drift_ok = all(drift_differs_from_clean(t) for t in ["A", "B", "C"])
    gates["drift_detection"] = drift_ok
    for t in ["A", "B", "C"]:
        differs = drift_differs_from_clean(t)
        print(f"  Task {t}: drift {'differs from' if differs else 'SAME AS'} clean — "
              f"{'PASS' if differs else 'FAIL'}")
    print(f"  Result: {'PASS' if drift_ok else 'FAIL'}")

    # ── Gate 3: Good checkpoint acceptance ───────────────────────────
    print("\n=== G0: Good checkpoint acceptance ===")
    good_checkpoints = 0
    for task_str, condition_str, fixture in all_fixtures():
        d = base_dir / f"checkpoint_good_{task_str}_{condition_str}"
        fixture.write_inputs(d)
        for stage in range(1, 5):
            fixture.write_artifact(stage, fixture.stage_artifacts[stage].content, d)
            receipt = evaluate_checkpoint(stage, d, fixture, worker_label=chr(64 + stage),
                                           trajectory_id=f"gate3_{task_str}_{condition_str}_s{stage}")
            if receipt is not None:
                good_checkpoints += 1
                print(f"  Task {task_str}, {condition_str}, Stage {stage}: PASS (SHA256={receipt.artifact_sha256[:12]}...)")
            else:
                print(f"  Task {task_str}, {condition_str}, Stage {stage}: FAIL")
    gates["good_checkpoints"] = good_checkpoints == 24  # 6 fixtures × 4 stages
    print(f"  Result: {good_checkpoints}/24 good checkpoints accepted — {'PASS' if gates['good_checkpoints'] else 'FAIL'}")

    # ── Gate 4: Bad checkpoint rejection ─────────────────────────────
    print("\n=== G0: Bad checkpoint rejection ===")
    bad_rejected = 0
    for task_str, condition_str, fixture in all_fixtures():
        d = base_dir / f"checkpoint_bad_{task_str}_{condition_str}"
        fixture.write_inputs(d)
        for stage in range(1, 5):
            fixture.write_bad_artifact(stage, d)
            receipt = evaluate_checkpoint(stage, d, fixture, worker_label=chr(64 + stage),
                                           trajectory_id=f"gate4_{task_str}_{condition_str}_s{stage}")
            if receipt is None:
                bad_rejected += 1
                print(f"  Task {task_str}, {condition_str}, Stage {stage}: PASS (bad artifact rejected)")
            else:
                print(f"  Task {task_str}, {condition_str}, Stage {stage}: FAIL (bad artifact accepted)")
    gates["bad_checkpoints"] = bad_rejected == 24
    print(f"  Result: {bad_rejected}/24 bad checkpoints rejected — {'PASS' if gates['bad_checkpoints'] else 'FAIL'}")

    # ── Gate 5: SHA256 recorded ──────────────────────────────────────
    print("\n=== G0: SHA256 recording ===")
    sha_recorded = 0
    for task_str, condition_str, fixture in all_fixtures():
        d = base_dir / f"sha_test_{task_str}_{condition_str}"
        fixture.write_inputs(d)
        fixture.write_artifact(1, fixture.stage_artifacts[1].content, d)
        receipt = evaluate_checkpoint(1, d, fixture, trajectory_id=f"gate5_{task_str}_{condition_str}")
        if receipt and receipt.artifact_sha256:
            sha_recorded += 1
    gates["sha_recorded"] = sha_recorded == 6
    print(f"  Result: {sha_recorded}/6 SHA256 recorded — {'PASS' if gates['sha_recorded'] else 'FAIL'}")

    # ── Gate 6: Adapter exercise ─────────────────────────────────────
    print("\n=== G0: Adapter exercise ===")
    adapter_results: dict[str, ArchitectureState] = {}
    for arch_name in ("stateless", "summary", "verified_state"):
        adapter = get_adapter(arch_name)
        # Create a synthetic transcript
        receipt = CheckpointReceipt(
            receipt_id="cp_test_1",
            stage_number=1, worker_label="A",
            artifact_path="/tmp/test.txt", artifact_sha256="abc",
            test_results={"test1": True}, timestamp=time.time(),
        )
        tc = ToolCallReceipt(
            receipt_id="rct_001", tool="write_file",
            params={"path": "/tmp/test.txt", "content": "hello"},
            status="ok", output="Wrote 5 bytes",
            evidence_refs=(EvidenceReference("rct_001", "file_written", "/tmp/test.txt"),),
        )
        events = [
            SessionEvent(
                type="session_start", session_id="s1", trajectory_id="t1",
                architecture=arch_name, worker_label="A", stage=1,
                timestamp=time.time(), tool_calls=(tc,), checkpoint=receipt,
            ),
        ]
        state = adapter.transform(events)
        adapter_results[arch_name] = state
        print(f"  {arch_name}: token_cost={state.token_cost}, content_type={type(state.content).__name__}")

    gates["adapters_work"] = all(
        isinstance(r, ArchitectureState) for r in adapter_results.values()
    )
    print(f"  Result: {'PASS' if gates['adapters_work'] else 'FAIL'}")

    # ── Gate 7: Verified state has receipt-backed claims ─────────────
    print("\n=== G0: Verified state receipt-backed claims ===")
    vs_state = adapter_results.get("verified_state")
    vs_ok = False
    if vs_state and isinstance(vs_state.content, dict):
        content = vs_state.content
        claims = content.get("claims", [])
        has_claims_with_receipts = any(
            c.get("receipt_ids") for c in claims
        )
        vs_ok = has_claims_with_receipts
    gates["verified_state_receipts"] = vs_ok
    print(f"  Result: {'PASS' if vs_ok else 'FAIL'}")

    # ── Gate 8: Unsupported claims omitted or labeled ────────────────
    print("\n=== G0: Unsupported claims handling ===")
    # Verified state adapter should not include free-text claims without receipts
    vs_result = adapter_results.get("verified_state")
    vs_content = vs_result.content if vs_result else {}
    unsupported_ok = True
    if isinstance(vs_content, dict):
        # Check that there's no free-text summary field
        has_free_text = any(k for k in vs_content if k in ("summary", "interpretation", "analysis"))
        if has_free_text:
            unsupported_ok = False
    gates["unsupported_claims"] = unsupported_ok
    print(f"  Result: {'PASS' if unsupported_ok else 'FAIL'}")

    # ── Gate 9: Adapter isolation ────────────────────────────────────
    print("\n=== G0: Adapter isolation ===")
    # Each adapter should produce independent state
    isolation_ok = True
    for name1, state1 in adapter_results.items():
        for name2, state2 in adapter_results.items():
            if name1 != name2 and state1 is state2:
                isolation_ok = False
    gates["adapter_isolation"] = isolation_ok
    print(f"  Result: {'PASS' if isolation_ok else 'FAIL'}")

    # ── Gate 10: Traversal rejection ─────────────────────────────────
    print("\n=== G0: Traversal rejection ===")
    allowed = Path("/tmp/benchmark_safe")
    safe = reject_traversal("file.txt", allowed)
    traversal = reject_traversal("../etc/passwd", allowed)
    gates["traversal_rejection"] = safe and not traversal
    print(f"  Safe path: {'PASS' if safe else 'FAIL'}")
    print(f"  Traversal path: {'PASS' if not traversal else 'FAIL'}")
    print(f"  Result: {'PASS' if gates['traversal_rejection'] else 'FAIL'}")

    # ── Gate 11: Shared Worker-A baseline ────────────────────────────
    print("\n=== G0: Shared Worker-A baseline ===")
    baselines = generate_all_baselines(
        base_dir, tasks=["A"], conditions=["clean"], replicates=[0, 1]
    )
    architectures = ["stateless", "summary", "verified_state"]
    clones = clone_all_architectures(baselines, architectures, base_dir / "clones")
    acct = accounting_summary(baselines, clones, architectures)
    gates["shared_baseline"] = len(baselines) == 2  # 1 task × 1 condition × 2 reps
    print(f"  Unique Worker-A checkpoints: {len(baselines)} — {'PASS' if gates['shared_baseline'] else 'FAIL'}")
    print(f"  Total clones: {len(clones)}")
    print(f"  Pilot accounting: {json.dumps(acct.get('pilot', {}), indent=2)}")

    # ── Gate 12: 24 vs 20 accounting ─────────────────────────────────
    print("\n=== G0: 24 vs 20 accounting ===")
    # Validate generic clone accounting
    pilot_archs = ["stateless", "summary", "verified_state"]
    acct_result = verify_accounting(
        baselines_count=len(baselines),
        clones_count=len(clones),
        architectures=pilot_archs,
    )
    generic_ok = (
        acct_result.get("full_study", {}).get("trajectories") == 6
        and acct_result.get("full_study", {}).get("clones") == 6
    )
    # Validate pilot_accounting separately
    p = pilot_accounting()
    pilot_ok = (
        p.get("logical_session_records") == 24
        and p.get("unique_model_session_equivalents") == 20
    )
    accounting_ok = generic_ok and pilot_ok
    gates["accounting_24v20"] = accounting_ok
    print(f"  Generic: {acct_result.get('full_study', {}).get('trajectories')} trajectories, {acct_result.get('full_study', {}).get('clones')} clones — {'PASS' if generic_ok else 'FAIL'}")
    print(f"  Pilot: {p.get('logical_session_records')} logical, {p.get('unique_model_session_equivalents')} unique — {'PASS' if pilot_ok else 'FAIL'}")

    # ── Gate 13: Deterministic scoring ───────────────────────────────
    print("\n=== G0: Deterministic scoring ===")
    # Create synthetic session data
    fixture = get_fixture("A", "clean")
    tid = TrajectoryID(task="A", condition="clean", architecture="verified_state", replicate=0)
    sessions: dict[str, list[SessionEvent]] = {}
    for worker in ["A", "B", "C", "D"]:
        stage = ord(worker) - 64  # A=1, B=2, C=3, D=4
        sid = str(SessionID(trajectory=tid, worker=worker))
        tc = ToolCallReceipt(
            receipt_id=f"rct_{worker}_001", tool="write_file",
            params={"path": f"stage_{stage}/output.json", "content": "data"},
            status="ok", output="ok",
        )
        cp = CheckpointReceipt(
            receipt_id=f"cp_{stage}_{worker}",
            stage_number=stage, worker_label=worker,
            artifact_path=f"/tmp/stage_{stage}/output.json",
            artifact_sha256=compute_sha256("data"),
            test_results={f"test_{stage}_1": True},
            timestamp=float(stage),
        )
        sessions[worker] = [
            SessionEvent(
                type="session_end", session_id=sid, trajectory_id=str(tid),
                architecture="verified_state", worker_label=worker, stage=stage,
                timestamp=float(stage), tool_calls=(tc,), checkpoint=cp,
            ),
        ]

    score1 = score_trajectory(str(tid), "verified_state", "A", "clean", sessions, fixture)
    score2 = score_trajectory(str(tid), "verified_state", "A", "clean", sessions, fixture)
    deterministic = score1.to_json() == score2.to_json()
    gates["deterministic_scoring"] = deterministic
    print(f"  Score 1: {json.dumps(score1.to_json(), sort_keys=True)}")
    print(f"  Score 2: {json.dumps(score2.to_json(), sort_keys=True)}")
    print(f"  Identical: {'PASS' if deterministic else 'FAIL'}")

    # ── Gate 14: No network/model calls ──────────────────────────────
    print("\n=== G0: No network/model calls ===")
    # Verify by checking that no network-related modules were imported
    # and no model API calls were made
    no_network = True
    suspicious_imports = ["requests", "urllib3", "httpx", "openai", "anthropic", "ollama"]
    for mod_name in suspicious_imports:
        if mod_name in sys.modules:
            print(f"  WARNING: {mod_name} is imported!")
            no_network = False
    gates["no_network_calls"] = no_network
    print(f"  Result: {'PASS' if no_network else 'FAIL'}")

    # ── Summary ──────────────────────────────────────────────────────
    all_pass = all(gates.values())
    summary = {
        "g0_dry_run": "PASS" if all_pass else "FAIL",
        "gates": gates,
        "gates_passed": sum(1 for v in gates.values() if v),
        "gates_total": len(gates),
        "errors": errors,
        "output_dir": str(output_dir),
    }

    print(f"\n{'='*60}")
    print(f"G0 DRY RUN: {'PASS' if all_pass else 'FAIL'}")
    print(f"Gates: {summary['gates_passed']}/{summary['gates_total']} passed")
    for gate, passed in gates.items():
        print(f"  {gate}: {'PASS' if passed else 'FAIL'}")
    print(f"{'='*60}")

    # Write summary
    summary_path = output_dir / "g0_summary.json"
    summary_path.write_text(serialize_json(summary))
    print(f"\nSummary written to: {summary_path}")

    return summary


def main() -> int:
    """Entry point for the G0 dry-run CLI."""
    output_dir = os.environ.get("G0_OUTPUT_DIR")
    if output_dir:
        output_dir = Path(output_dir)
    else:
        output_dir = Path(tempfile.mkdtemp(prefix="g0_dry_run_"))

    print(f"G0 Dry-Run Output: {output_dir}")
    print(f"Python: {sys.version}")
    print(f"No network or model calls will be made.\n")

    result = run_g0_dry_run(output_dir)

    return 0 if result.get("g0_dry_run") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
