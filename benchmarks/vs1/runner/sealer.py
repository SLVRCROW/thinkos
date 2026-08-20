"""VS-1 evidence sealer — immutable raw evidence before interpretation.

Seals: prompts, provider receipts, artifacts, hidden-test results, scores,
schedule, run record. Builds a MANIFEST.sha256 over every raw file and
verifies readback. The sealed bundle is the frozen evidence base; analysis
reads only from it.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchmarks.vs1.schemas import compute_sha256, json_dumps


class EvidenceSealer:
    def __init__(self, evidence_root: Path):
        self.root = evidence_root
        self.raw = evidence_root / "raw"
        self.raw.mkdir(parents=True, exist_ok=True)
        self.ledger_path = evidence_root / "CALL_LEDGER.jsonl"

    def write_prompt(self, cell_id: str, prompt_text: str, prompt_sha: str) -> Path:
        p = self.raw / f"{cell_id}.prompt.txt"
        p.write_text(prompt_text)
        return p

    def write_provider(self, cell_id: str, provider_json: dict) -> Path:
        p = self.raw / f"{cell_id}.provider.json"
        p.write_text(json_dumps(provider_json))
        return p

    def write_raw_completion(self, cell_id: str, raw_content: str) -> Path:
        """R4: persist the RAW provider content BEFORE parsing/scoring.

        A parser failure must NEVER erase the model output. This is the
        instrument's ear: what the model actually said survives regardless
        of parse success, evaluation, or mid-run halt.
        """
        p = self.raw / f"{cell_id}.raw.txt"
        p.write_text(raw_content)
        return p

    def write_artifact(self, cell_id: str, artifact_text: str) -> Path:
        p = self.raw / f"{cell_id}.artifact.txt"
        p.write_text(artifact_text)
        return p

    def write_cell(self, cell_id: str, outcome_json: dict) -> Path:
        p = self.raw / f"{cell_id}.outcome.json"
        p.write_text(json_dumps(outcome_json))
        return p

    def append_ledger(self, entry: dict) -> None:
        """R4: append-only call ledger. Every provider invocation gets one
        line, written immediately after the call returns. A mid-run halt
        leaves every completed call reconstructible from this ledger.
        """
        with open(self.ledger_path, "a", encoding="utf-8") as f:
            f.write(json_dumps(entry) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def seal(
        self,
        run_metadata: dict,
        outcomes: list[dict],
        prompts: dict[str, str],
        schedule: dict,
    ) -> Path:
        """Write all raw files, build manifest, verify readback, return root."""
        # Persist every outcome as a raw file
        for o in outcomes:
            self.write_cell(o["trajectory_id"], o)
        # Persist every prompt (full text from the outcome records)
        for o in outcomes:
            cell_id = o["trajectory_id"]
            text = prompts.get(cell_id, "")
            self.write_prompt(cell_id, text, compute_sha256(text))
        # Run metadata + schedule
        (self.raw / "run_metadata.json").write_text(json_dumps(run_metadata))
        (self.raw / "schedule.json").write_text(json_dumps(schedule))

        # Manifest over every raw file
        hashes = {}
        for f in sorted(self.raw.rglob("*")):
            if f.is_file():
                hashes[str(f.relative_to(self.raw))] = compute_sha256(f.read_text())
        manifest = {
            "root": str(self.root),
            "files": hashes,
            "manifest_sha256": compute_sha256(json_dumps(hashes)),
        }
        manifest_path = self.root / "MANIFEST.json"
        manifest_path.write_text(json_dumps(manifest))

        # Verify readback
        verified = self.verify(self.root)
        if not verified:
            raise RuntimeError("Evidence seal verification FAILED")
        return manifest_path

    @staticmethod
    def verify(root: Path) -> bool:
        manifest_path = root / "MANIFEST.json"
        if not manifest_path.exists():
            return False
        manifest = json.loads(manifest_path.read_text())
        for rel, expected in manifest["files"].items():
            f = root / "raw" / rel
            if not f.exists() or compute_sha256(f.read_text()) != expected:
                return False
        return True
