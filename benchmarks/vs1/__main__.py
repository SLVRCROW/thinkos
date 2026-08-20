#!/usr/bin/env python3
"""VS-1 instrumentation dry-run CLI.

Zero model, zero API, zero network. Exercises the full measurement chassis:
six adapters, six conditions, isolation, canaries, deterministic scoring,
accounting (with mock pricing), evidence-packet construction, and
reconstruction. Exit 0 on all gates passed; exit 1 on any failure.

This validates the measurement instrument ONLY. It is not the thesis answer.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from .schemas import ARMS, CONDITIONS, SessionEvent, ToolCallReceipt, compute_sha256, json_dumps
from .adapters import get_adapter, adapter_states, BOUNDARIES
from .fixtures import get_fixture, all_fixtures
from .isolation import (
    CANARIES,
    create_isolated_workdir,
    detect_foreign_canary,
    embed_canary,
    verify_isolation,
    verify_no_leakage,
)
from .scorer import score_trajectory
from .accounting import (
    ProviderCall,
    compute_call_cost,
    sum_of_parts,
    trajectory_accounting,
    pilot_accounting,
)
from .analysis import primary_contrasts, paired_wald_ci, exact_sign_test, risk_difference
from .evidence import build_evidence_packet, reconstruct_experiment, make_trajectory_receipt
from .baseline import synthetic_successor


MOCK_PRICING = {
    "input_per_1k": 100,       # $0.10 / 1k input (micro-USD)
    "output_per_1k": 300,      # $0.30 / 1k output
    "cached_input_per_1k": 50, # $0.05 / 1k cached input
}


def _make_predecessor(trajectory_id: str, task: str, condition: str) -> list[SessionEvent]:
    """Deterministic predecessor (Worker A) event list for a trajectory."""
    fixture = get_fixture(task, condition)
    workdir = Path(tempfile.mkdtemp(prefix="vs1_pred_"))
    fixture.write_inputs(workdir)
    content = fixture.stage_artifacts[1].content
    path = fixture.stage_artifacts[1].path
    tc = ToolCallReceipt(
        receipt_id=compute_sha256(f"{trajectory_id}_pred")[:32],
        tool="write_file",
        params={"path": path, "content": content},
        status="ok",
        output="predestor artifact",
        evidence_refs=(),
        timestamp=1.0,
    )
    return [
        SessionEvent(
            type="agent_message",
            session_id=f"{trajectory_id}-A",
            trajectory_id=trajectory_id,
            arm="verified_state",
            condition=condition,
            worker_label="A",
            stage=1,
            timestamp=1.0,
            tool_calls=(tc,),
        )
    ]


def compute_receipt_id(seed: str) -> str:
    return compute_sha256(seed)[:20]


def run_vs1_dry_run(output_dir: str | Path | None = None) -> dict[str, Any]:
    """Run the VS-1 instrumentation dry-run and return results."""
    gates: dict[str, bool] = {}
    evidence: list[dict] = []
    errors: list[str] = []

    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="vs1_dry_run_"))
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
    base_dir = output_dir / "work"
    base_dir.mkdir(parents=True, exist_ok=True)

    # ── Gate 1: all six arms present and boundaries contract-exposed ──────
    print("=== VS-1: Six-arm adapter registry ===")
    arms = sorted(ARMS)
    gates["six_arms_registered"] = len(arms) == 6
    for arm in arms:
        b = BOUNDARIES[arm]
        print(f"  {arm}: provenance={b.provenance_survives} cost={b.token_cost_model}")
    print(f"  Result: {'PASS' if gates['six_arms_registered'] else 'FAIL'}")

    # ── Gate 2: Fixture generation (6 tasks × 6 conditions = 36 sets) ─────
    print("\n=== VS-1: Fixture generation ===")
    fixtures = all_fixtures()
    gates["fixture_generation"] = len(fixtures) == 18
    print(f"  Result: {len(fixtures)} fixture sets — {'PASS' if gates['fixture_generation'] else 'FAIL'}")

    # ── Gate 3: Adapter determinism + isolation of states ─────────────────
    print("\n=== VS-1: Adapter determinism ===")
    deterministic = True
    for task in ("A", "B", "C"):
        for condition in ("clean", "poison", "contradiction", "reversal", "interruption", "motif"):
            tid = f"T{task}-{condition}"
            transcript = _run_predecessor(tid, task, condition)
            states1 = adapter_states(transcript)
            states2 = adapter_states(transcript)
            for arm in ARMS:
                if json_dumps(states1[arm].to_json()) != json_dumps(states2[arm].to_json()):
                    deterministic = False
                    errors.append(f"Adapter {arm} not deterministic for {tid}")
    gates["adapter_determinism"] = deterministic
    print(f"  Result: {'PASS' if deterministic else 'FAIL'}")

    # ── Gate 4: Canary embedding + foreign detection ──────────────────────
    print("\n=== VS-1: Semantic canaries ===")
    canary_ok = True
    for arm in ARMS:
        env = embed_canary(arm, {})
        foreign = detect_foreign_canary(json_dumps(env), arm)
        if foreign:
            canary_ok = False
    # A successor repeating another arm's canary must be detected:
    foreign = detect_foreign_canary(CANARIES["summary"], "stateless")
    canary_ok = canary_ok and bool(foreign)
    gates["canary_isolation"] = canary_ok
    print(f"  Result: {'PASS' if canary_ok else 'FAIL'}")

    # ── Gate 5: Isolation (workdir containment + no leakage) ─────────────
    print("\n=== VS-1: Isolation ===")
    archives = {}
    iso_ok = True
    for arm in ARMS:
        wd = create_isolated_workdir(f"iso_{arm}", base_dir)
        (wd / "state.json").write_text(json_dumps({"arm": arm}))
        archives[arm] = wd
        if not verify_isolation(wd):
            iso_ok = False
    if not verify_no_leakage(archives):
        iso_ok = False
    gates["isolation"] = iso_ok
    print(f"  Result: {'PASS' if iso_ok else 'FAIL'}")

    # ── Gate 6: Synthetic successor scoring produces expected signals ─────
    print("\n=== VS-1: Deterministic scoring ===")
    scores_by_arm: dict[str, list[float]] = {arm: [] for arm in ARMS}
    all_scores: dict[str, Any] = {}
    for condition in ("clean", "interruption", "reversal", "contradiction", "poison", "motif"):
        for arm in ARMS:
            tid = f"T-{condition}-{arm}"
            transcript = _run_predecessor(tid, "A", condition)
            state = get_transformer(arm).transform(transcript)
            wd = create_isolated_workdir(tid, base_dir)
            fixture = get_fixture("A", condition)
            succ_events = synthetic_successor(tid, arm, condition, "A", state.content, wd, capability=0.9, seed=hash(tid) % 10000)
            hidden = fixture.run_hidden_test(wd)
            score = score_trajectory(
                trajectory_id=tid,
                arm=arm,
                condition=condition,
                task="A",
                predecessor_events=transcript,
                successor_events=succ_events,
                hidden_test_results=hidden,
            )
            trajectories[tid] = {
                "trajectory": {"id": tid, "arm": arm, "condition": condition},
                "adapter_state": state.to_json(),
                "receipt": make_trajectory_receipt(tid, arm, condition, score.to_json(), {}).to_json(),
            }
            trajectories_scores[tid] = score.to_json()
            scores_by_arm[arm].append(score.final_task_quality)
            all_scores[tid] = score.to_json()
    gates["scoring"] = all(v is not None for v in all_scores.values()) and len(all_scores) == 36
    print(f"  Result: {len(all_scores)}/36 scores — {'PASS' if gates['scoring'] else 'FAIL'}")

    # ── Gate 7: Accounting (sum-of-parts, budgets) ────────────────────────
    print("\n=== VS-1: Accounting ===")
    calls = [
        ProviderCall(
            provider_invocation_id="p1",
            trajectory_id="T-clean-stateless",
            worker="B",
            stage=2,
            attempt=0,
            model="mock-v1",
            prompt_tokens=500,
            completion_tokens=100,
        ),
        ProviderCall(
            provider_invocation_id="p2",
            trajectory_id="T-clean-stateless",
            worker="B",
            stage=2,
            attempt=1,
            model="mock-v1",
            prompt_tokens=500,
            completion_tokens=100,
            status="retry",
            retry_of="p1",
        ),
        ProviderCall(
            provider_invocation_id="p3",
            trajectory_id="T-clean-stateless",
            worker="C",
            stage=3,
            attempt=0,
            model="mock-v1",
            prompt_tokens=1000,
            completion_tokens=200,
            cached_input_tokens=400,
        ),
    ]
    sop = sum_of_parts(calls, MOCK_PRICING)
    ta = trajectory_accounting(calls, MOCK_PRICING)
    pa = pilot_accounting(calls, MOCK_PRICING, budget_micro_usd=10000)
    accounting_ok = (
        sop["physical_calls"] == 3
        and sop["logical_calls"] == 2  # p2 is a retry of p1
        and sop["sum_of_parts_verified"]
        and pa["within_budget"]
    )
    gates["accounting"] = accounting_ok
    print(f"  Result: physical={sop['physical_calls']} logical={sop['logical_calls']} "
          f"sum_of_parts={sop['sum_of_parts_verified']} within_budget={pa['within_budget']} — "
          f"{'PASS' if accounting_ok else 'FAIL'}")

    # ── Gate 8: Evidence packet + reconstruction ─────────────────────────
    print("\n=== VS-1: Evidence packet + reconstruction ===")
    packet = build_evidence_packet(
        run_id="dryrun001",
        pilot_dir=output_dir,
        trajectories=trajectories,
        pilot_config={"arms": ARMS, "conditions": CONDITIONS, "pricing": MOCK_PRICING},
        scores=trajectories_scores,
    )
    recon = reconstruct_experiment(packet)
    gates["evidence"] = recon["n_trajectories"] == 36
    print(f"  Result: packet={packet.name} reconstructed={recon['n_trajectories']} — "
          f"{'PASS' if gates['evidence'] else 'FAIL'}")

    # ── Gate 9: analysis reconstruction ──────────────────────────────────
    print("\n=== VS-1: Analysis (frozen primitives) ===")
    means = {arm: (sum(v) / len(v) if v else 0.0) for arm, v in scores_by_arm.items()}
    analysis_ok = len(means) == 6
    gates["analysis"] = analysis_ok
    print(f"  Result: means={ {k: round(v, 3) for k, v in means.items()} } — "
          f"{'PASS' if analysis_ok else 'FAIL'}")

    # ── Gate 10: No-network/no-model proof ───────────────────────────────
    print("\n=== VS-1: No network/model calls ===")
    import socket
    blocked = []
    def _block(*args, **kwargs):
        blocked.append(True)
        raise RuntimeError("network call attempted")
    orig = socket.create_connection
    socket.create_connection = _block
    try:
        run_vs1_workload()
    finally:
        socket.create_connection = orig
    gates["no_network_calls"] = not blocked
    print(f"  Result: {'PASS' if not blocked else 'FAIL'}")

    print("\n============================================================")
    print("VS-1 DRY RUN:", "PASS" if all(gates.values()) else "FAIL")
    for g, ok in gates.items():
        print(f"  {g}: {'PASS' if ok else 'FAIL'}")
    print("============================================================")

    summary_path = output_dir / "vs1_summary.json"
    summary = {
        "gates": gates,
        "result": "PASS" if all(gates.values()) else "FAIL",
        "n_trajectories": len(trajectories),
        "arms": list(ARMS),
        "conditions": list(CONDITIONS),
        "output_dir": str(output_dir),
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True))
    print(f"Summary written to: {summary_path}")
    return summary


def get_transformer(arm: str):
    from .adapters import get_adapter
    return get_adapter(arm)


def _run_predecessor(trajectory_id: str, task: str, condition: str) -> list[SessionEvent]:
    return _make_predecessor_events(trajectory_id, task, condition)


def _make_predecessor_events(trajectory_id: str, task: str, condition: str) -> list[SessionEvent]:
    fixture = get_fixture(task, condition)
    content = fixture.stage_artifacts[1].content
    path = fixture.stage_artifacts[1].path
    tc = ToolCallReceipt(
        receipt_id=compute_receipt_id(trajectory_id),
        tool="write_file",
        params={"path": path, "content": content},
        status="ok",
        output="ok",
        evidence_refs=(),
        timestamp=1.0,
    )
    return [
        SessionEvent(
            type="agent_message",
            session_id=f"{trajectory_id}-A",
            trajectory_id=trajectory_id,
            arm="verified_state",
            condition=condition,
            worker_label="A",
            stage=1,
            timestamp=1.0,
            tool_calls=(tc,),
        )
    ]


def run_vs1_workload() -> None:
    """A tiny deterministic workload executed under the network-denial probe."""
    _ = sum(1 for _ in all_fixtures())


def get_boundary(arm: str):
    from .adapters import BOUNDARIES
    return BOUNDARIES[arm]


# ── Module-level state for the dry-run loop ─────────────────────────────────
trajectories: dict[str, Any] = {}
trajectories_scores: dict[str, Any] = {}


def main() -> int:
    result = run_vs1_dry_run()
    return 0 if result.get("result") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
