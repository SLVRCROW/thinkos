"""VS-1 evidence receipts: immutable receipts, evidence-packet construction.

Every VS-1 trajectory produces receipts. The evidence packet is the
reconstruction contract: given only the frozen artifacts, an independent
reviewer can rebuild the experiment's recorded state, verify hashes, and
recompute scores deterministically.
"""
from __future__ import annotations

import dataclasses
import json
import re
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
        MANIFEST.sha256
        pilot_receipt.json

    Path containment (Codex C10): run_id and trajectory IDs are validated
    with a strict allowlist; every resolved path is verified relative to the
    packet base before creation or deletion.
    """
    if not _SAFE_ID_RE.match(run_id):
        raise ValueError(f"Unsafe run_id: {run_id!r}")
    for tid in trajectories:
        if not _SAFE_ID_RE.match(tid):
            raise ValueError(f"Unsafe trajectory_id: {tid!r}")

    base = Path(pilot_dir).resolve()
    base.mkdir(parents=True, exist_ok=True)
    root = base / f"pilot_{run_id}"
    if root.exists():
        # Only remove if root is strictly inside base (containment guard).
        if not _is_subpath(root, base):
            raise ValueError("Refusing to remove a packet root outside pilot_dir")
        shutil.rmtree(root)
    root.mkdir()

    (root / "pilot_config.json").write_text(json_dumps(pilot_config))
    (root / "pilot_scores.json").write_text(json_dumps(scores))

    traj_root = root / "trajectories"
    traj_root.mkdir()

    hashes: dict[str, str] = {}
    for tid, data in trajectories.items():
        tdir = traj_root / f"trajectory_{tid}"
        tdir.mkdir()
        (tdir / "trajectory.json").write_text(json_dumps(data.get("trajectory", {})))
        (tdir / "adapter.json").write_text(json_dumps(data.get("adapter_state", {})))
        (tdir / "receipt.json").write_text(json_dumps(data.get("receipt", {})))
        # Daedalus F7: persist the per-trajectory event log (predecessor +
        # successor SessionEvents) so an independent reviewer can reconstruct
        # the exact tool-call sequence and scoring inputs.
        event_log = []
        for ev in data.get("predecessor_events", []) + data.get("successor_events", []):
            event_log.append(ev.to_json() if hasattr(ev, "to_json") else ev)
        (tdir / "events.jsonl").write_text("\n".join(json_dumps(e) for e in event_log))
        for f in tdir.iterdir():
            if f.is_file():
                hashes[str(f.relative_to(root))] = compute_sha256(f.read_text())

    # Hash ALL immutable packet files, including config/scores (Codex C11).
    for f in (root / "pilot_config.json", root / "pilot_scores.json"):
        hashes[str(f.relative_to(root))] = compute_sha256(f.read_text())

    manifest_text = "\n".join(f"{h}  {p}" for p, h in sorted(hashes.items())) + "\n"
    (root / "MANIFEST.sha256").write_text(manifest_text)

    # Pilot receipt: content_hash is a SHA-256 of the canonical manifest
    # (Codex C11: raw JSON is not a hash).
    receipt = {
        "receipt_id": f"rct_{run_id}",
        "kind": "pilot",
        "content_hash": compute_sha256(manifest_text),
        "created_at": "",
        "metadata": {"run_id": run_id, "n_trajectories": len(trajectories)},
    }
    (root / "pilot_receipt.json").write_text(json_dumps(receipt))
    return root


_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _is_subpath(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def reconstruct_experiment(packet_root: str | Path) -> dict[str, Any]:
    """Reconstruct the experiment from a packet.

    Fails closed (Codex C11): verifies MANIFEST.sha256 against every file,
    refuses missing/extra/mismatched files, and verifies the pilot receipt's
    content_hash.
    """
    root = Path(packet_root).resolve()
    manifest_path = root / "MANIFEST.sha256"
    if not manifest_path.exists():
        raise ValueError("Evidence packet missing MANIFEST.sha256")
    manifest: dict[str, str] = {}
    for line in manifest_path.read_text().splitlines():
        if not line.strip():
            continue
        h, _, p = line.strip().partition("  ")
        manifest[p] = h
    if not manifest:
        raise ValueError("Evidence packet manifest empty")

    # Verify every listed file exists and matches; refuse extra files.
    actual = {}
    for f in sorted(root.rglob("*")):
        if f.is_file() and f != manifest_path and f.name != "pilot_receipt.json":
            actual[str(f.relative_to(root))] = compute_sha256(f.read_text())
    for p, h in manifest.items():
        if actual.get(p) != h:
            raise ValueError(f"Manifest mismatch for {p}: expected {h}, got {actual.get(p)}")
    for p in actual:
        if p not in manifest:
            raise ValueError(f"File not in manifest: {p}")

    receipt = json.loads((root / "pilot_receipt.json").read_text())
    if receipt.get("content_hash") != compute_sha256(manifest_path.read_text()):
        raise ValueError("pilot_receipt.content_hash does not match MANIFEST.sha256")

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
