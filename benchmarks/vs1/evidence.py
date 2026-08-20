"""VS-1 evidence receipts: immutable receipts, evidence-packet construction.

Every VS-1 trajectory produces receipts. The evidence packet is the
reconstruction contract: given only the frozen artifacts, an independent
reviewer can rebuild the experiment's recorded state, verify hashes, and
recompute scores deterministically.
"""
from __future__ import annotations

import dataclasses
import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .schemas import ARMS, CONDITIONS, compute_sha256, json_dumps


@dataclass(frozen=True)
class Receipt:
    receipt_id: str
    kind: str  # trajectory | pilot | score | evidence
    content_hash: str
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "receipt_id": self.receipt_id,
            "kind": self.kind,
            "content_hash": self.content_hash,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


def make_trajectory_receipt(
    trajectory_id: str,
    arm: str,
    condition: str,
    score: dict[str, Any],
    content: dict[str, Any],
) -> Receipt:
    content_hash = compute_sha256(json_dumps({"score": score, "artifacts": content}))
    return Receipt(
        receipt_id=f"rct_{trajectory_id[:24]}",
        kind="trajectory",
        content_hash=content_hash,
        metadata={"trajectory_id": trajectory_id, "arm": arm, "condition": condition},
    )


def build_evidence_packet(
    run_id: str,
    pilot_dir: str | Path,
    trajectories: dict[str, dict[str, Any]],
    pilot_config: dict[str, Any],
    scores: dict[str, Any],
) -> Path:
    """Build a VS-1 evidence packet and return its root path.

    Layout mirrors G1 contract §20 shape but scaled to six arms/six conditions:
      pilot_{ID}/
        pilot_config.json
        pilot_scores.json
        trajectories/
          trajectory_{ID}/
            config.json
            score.json
            events.jsonl
            receipts.json
        pilot_receipt.json
    """
    root = Path(pilot_dir) / f"pilot_{run_id}"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    (root / "pilot_config.json").write_text(json_dumps(pilot_config))
    (root / "pilot_scores.json").write_text(json_dumps(scores))

    traj_root = root / "trajectories"
    traj_root.mkdir()

    hashes: dict[str, str] = {}
    for tid, data in trajectories.items():
        tdir = traj_root / f"trajectory_{tid}"
        tdir.mkdir(parents=True)
        (tdir / "trajectory.json").write_text(json_dumps(data.get("trajectory", {})))
        (tdir / "adapter.json").write_text(json_dumps(data.get("adapter_state", {})))
        (tdir / "receipt.json").write_text(json_dumps(data.get("receipt", {})))
        for f in tdir.iterdir():
            if f.is_file():
                hashes[str(f.relative_to(root))] = compute_sha256(f.read_text())

    (root / "MANIFEST.sha256").write_text(
        "\n".join(f"{h}  {p}" for p, h in sorted(hashes.items())) + "\n"
    )

    receipt = {
        "receipt_id": f"rct_{run_id}",
        "kind": "pilot",
        "content_hash": json_dumps(sorted(hashes.items())),
        "created_at": "",
        "metadata": {"run_id": run_id, "n_trajectories": len(trajectories)},
    }
    (root / "pilot_receipt.json").write_text(json_dumps(receipt))
    return root


def reconstruct_experiment(packet_root: str | Path) -> dict[str, Any]:
    """Reconstruct the experiment from a packet (validation of completeness)."""
    root = Path(packet_root)
    config = json.loads((root / "pilot_config.json").read_text())
    scores = json.loads((root / "pilot_scores.json").read_text())
    traj = {}
    tdir = root / "trajectories"
    if tdir.exists():
        for sub in sorted(tdir.iterdir()):
            if sub.is_dir():
                tid = sub.name.replace("trajectory_", "")
                traj[tid] = {
                    "trajectory": json.loads((sub / "trajectory.json").read_text()),
                    "adapter_state": json.loads((sub / "adapter.json").read_text()),
                    "receipt": json.loads((sub / "receipt.json").read_text()),
                }
    return {
        "pilot_config": config,
        "pilot_scores": scores,
        "trajectories": traj,
        "n_trajectories": len(traj),
    }
