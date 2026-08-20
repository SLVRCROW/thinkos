#!/usr/bin/env python3
"""VS-1 powered launcher — single frozen entry point.

Usage:
  python -m benchmarks.vs1.runner.launch --schedule <path> --out <dir> [--probe]

Flow:
  1. Load frozen schedule (must validate: 108 calls, 0 retries).
  2. Build the provider adapter (frozen model, temp 0, retries 0).
  3. (--probe) one preflight probe to confirm model identity + usage.
  4. Execute every cell (one provider call per cell, hard ceiling).
  5. Seal raw evidence (prompts, receipts, artifacts, outcomes, manifest).
  6. Write run_record.json with call accounting and verdict inputs.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from benchmarks.vs1.runner.executor import PoweredExecutor
from benchmarks.vs1.runner.provider import OllamaCloudAdapter
from benchmarks.vs1.runner.schedule import validate_schedule
from benchmarks.vs1.runner.sealer import EvidenceSealer

FROZEN_MODEL = "deepseek-v4-pro:0813"
FROZEN_TEMP = 0.0
FROZEN_MAX_TOKENS = 4096
FROZEN_REPLICATES = 3
# R3 topology (Marc act AUTHORIZE_VS1_R3...): 108 trajectories, 126 calls
EXPECTED_TRAJECTORIES = 108
EXPECTED_CALLS = 126


def main() -> int:
    parser = argparse.ArgumentParser(description="VS-1 powered launcher")
    parser.add_argument("--schedule", required=True, type=Path, help="EXECUTION_SCHEDULE.json path")
    parser.add_argument("--out", required=True, type=Path, help="Evidence output root")
    parser.add_argument("--probe", action="store_true", help="run one preflight probe then exit")
    args = parser.parse_args()

    schedule = json.loads(args.schedule.read_text())
    if not validate_schedule(schedule):
        print("FATAL: schedule invalid (must be 108 trajectories, 126 calls, 0 retries, 0 replacements)")
        return 2
    expected = schedule["expected_calls"]
    trajectories = schedule["trajectories"]
    print(f"Schedule validated: {trajectories} trajectories, {expected} calls, retries=0, replacements=0")

    provider = OllamaCloudAdapter(
        model=FROZEN_MODEL,
        temperature=FROZEN_TEMP,
        max_tokens=FROZEN_MAX_TOKENS,
    )

    if args.probe:
        res = provider.complete("Reply with exactly: PROBE_OK", "probe-001")
        print(f"PROBE status={res.status} model={res.returned_model} tokens={res.total_tokens}")
        print(f"PROBE content={res.content[:40]!r}")
        if res.status != "ok" or res.returned_model != FROZEN_MODEL:
            print("FATAL: probe failed; do not launch")
            return 1
        print("PROBE_OK — model identity and usage verified")
        return 0

    workdir = args.out / "work"
    workdir.mkdir(parents=True, exist_ok=True)

    executor = PoweredExecutor(
        provider=provider,
        schedule=schedule,
        workdir=workdir,
        model=FROZEN_MODEL,
    )
    run = executor.run()

    # Collect raw evidence
    sealer = EvidenceSealer(args.out)
    prompts = {}
    for o in executor.results:
        prompts[o.trajectory_id] = o.prompt_text
    manifest_path = sealer.seal(
        run_metadata={
            "model": FROZEN_MODEL,
            "temperature": FROZEN_TEMP,
            "max_tokens": FROZEN_MAX_TOKENS,
            "replicates": FROZEN_REPLICATES,
            "planned_calls": expected,
            "actual_calls": run["call_count"],
            "retries": 0,
            "replacements": 0,
        },
        outcomes=run["outcomes"],
        prompts=prompts,
        schedule=schedule,
    )
    print(f"Evidence sealed: {manifest_path}")
    print(f"Run record: {run['call_count']}/{run['planned']} calls, model={run['model']}")

    # Token/cost accounting
    total_in = sum(o["provider"]["prompt_tokens"] for o in run["outcomes"])
    total_out = sum(o["provider"]["completion_tokens"] for o in run["outcomes"])
    total = sum(o["provider"]["total_tokens"] for o in run["outcomes"])
    print(f"Tokens: in={total_in} out={total_out} total={total}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
