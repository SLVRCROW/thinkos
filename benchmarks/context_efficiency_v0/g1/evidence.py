"""G1-B evidence-packet construction and independent reconstruction.

The builder accepts a complete synthetic packet input, validates it before
writing, builds into a staging directory, validates that directory using only
recorded files, and atomically publishes it under the requested run root.
No provider or network code is imported.
"""

from __future__ import annotations

import dataclasses
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from benchmarks.context_efficiency_v0 import schemas as g0_schemas

from . import g0_manifest
from . import hashing
from . import schemas
from . import serialization


CONTRACT_SHA256 = "4400ae315386812049431f359447dfbce74fb208caf9f0e0625b77826172d6f6"
CONDITIONS = ("clean", "drift")
ARCHITECTURES = ("stateless", "summary", "verified_state")
WORKERS = ("B", "C", "D")
WORKER_STAGES = {"B": 2, "C": 3, "D": 4}
TRAJECTORY_ORDER = tuple(
    f"A-{condition}-{architecture}"
    for condition in CONDITIONS
    for architecture in ARCHITECTURES
)
RESULT_STATUSES = frozenset({"COMPLETE", "PARTIAL", "FAILED_POLICY", "ABORTED", "NOT_STARTED"})
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RFC3339_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")

PILOT_FILES = frozenset({
    "pilot_config.json",
    "pilot_accounting.json",
    "pilot_scores.json",
    "pilot_result.json",
    "pricing_catalog.json",
    "provider_selection.md",
    "pilot_receipt.json",
})
SHARED_FILES = frozenset({"provider_call_receipt.json", "checkpoint_receipt.json"})
TRAJECTORY_FILES = frozenset({
    "config.json",
    "worker_A_shared_source_ref.json",
    "trajectory_score.json",
    "trajectory_result.json",
    "trajectory_receipt.json",
})
WORKER_FILES = frozenset({"provider_call_receipt.json", "checkpoint_receipt.json"})
RAW_FILES = frozenset({"prompt.txt", "response.txt"})


@dataclasses.dataclass(frozen=True)
class EvidencePacketInput:
    pilot_id: str
    run_id: str
    pilot_config: dict[str, Any]
    pilot_accounting: dict[str, Any]
    pilot_scores: dict[str, Any]
    pilot_result: dict[str, Any]
    pricing_catalog: dict[str, Any]
    provider_selection_md: str
    shared_sources: dict[str, dict[str, Any]]
    trajectories: dict[str, dict[str, Any]]


@dataclasses.dataclass(frozen=True)
class EvidencePacketResult:
    packet_valid: bool
    packet_path: str | None
    shared_source_count: int
    trajectory_count: int
    reference_count: int
    errors: tuple[str, ...] = ()


def build_pilot_evidence(run_root: str | Path, packet: EvidencePacketInput) -> EvidencePacketResult:
    """Validate, construct, independently verify, and publish a packet."""
    run_root = Path(run_root)
    if not isinstance(packet, EvidencePacketInput):
        return EvidencePacketResult(False, None, 0, 0, 0, ("packet must be EvidencePacketInput",))
    errors = _validate_component(packet.pilot_id, "pilot_id")
    try:
        errors.extend(_validate_packet_input(packet))
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        errors.append(f"invalid packet input: {exc}")
    errors.extend(_validate_target(run_root, packet.pilot_id))
    if errors:
        return _result(packet, None, errors)

    run_root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{packet.pilot_id}.staging-", dir=run_root))
    target = run_root / packet.pilot_id
    try:
        _write_packet(stage, packet)
        disk_errors = validate_pilot_evidence_from_disk(stage)
        if disk_errors:
            return _result(packet, None, disk_errors)
        stage.rename(target)
        return _result(packet, target, [])
    except (OSError, ValueError, TypeError) as exc:
        return _result(packet, None, [f"packet construction failed: {exc}"])
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def validate_pilot_evidence_from_disk(pilot_dir: str | Path) -> list[str]:
    """Reconstruct and validate a packet using disk contents only."""
    root = Path(pilot_dir)
    errors: list[str] = []
    if not root.is_dir():
        return ["pilot directory does not exist"]

    _require_names(root, PILOT_FILES | {"shared_sources", "trajectories"}, "pilot", errors)
    shared_root = root / "shared_sources"
    trajectory_root = root / "trajectories"
    if not shared_root.is_dir() or not trajectory_root.is_dir():
        return errors
    _require_names(shared_root, frozenset(CONDITIONS), "shared_sources", errors)
    _require_names(trajectory_root, frozenset(TRAJECTORY_ORDER), "trajectories", errors)

    loaded: dict[str, Any] = {}
    for name in PILOT_FILES - {"provider_selection.md"}:
        loaded[name] = _read_json(root / name, name, errors)
    selection = _read_text(root / "provider_selection.md", "provider_selection.md", errors)
    if not selection.strip():
        errors.append("provider_selection.md must contain non-empty Markdown")

    pilot_config = loaded.get("pilot_config.json")
    pilot_accounting = loaded.get("pilot_accounting.json")
    pilot_scores = loaded.get("pilot_scores.json")
    pilot_result = loaded.get("pilot_result.json")
    pricing_catalog = loaded.get("pricing_catalog.json")
    pilot_receipt = loaded.get("pilot_receipt.json")
    if not isinstance(pilot_config, dict):
        return errors
    pilot_id = pilot_config.get("pilot_id")
    run_id = pilot_config.get("run_id")
    _validate_pilot_config(pilot_config, errors)
    _validate_pilot_accounting(pilot_accounting, errors)
    _validate_pilot_result(pilot_result, pilot_id, errors)
    _validate_pricing_catalog(pricing_catalog, errors)

    provider_receipts: list[dict[str, Any]] = []
    shared_receipt_ids: list[str] = []
    shared_source_ids: dict[str, str] = {}
    shared_invocation_ids: dict[str, str] = {}
    checkpoint_ids: dict[str, str] = {}
    for condition in CONDITIONS:
        directory = shared_root / condition
        _require_names(directory, SHARED_FILES | {"raw"}, f"shared_sources/{condition}", errors)
        raw = directory / "raw"
        _require_names(raw, RAW_FILES, f"shared_sources/{condition}/raw", errors)
        provider = _read_json(directory / "provider_call_receipt.json", "shared provider receipt", errors)
        checkpoint = _read_json(directory / "checkpoint_receipt.json", "shared checkpoint receipt", errors)
        prompt = _read_text(raw / "prompt.txt", "shared raw prompt", errors)
        response = _read_text(raw / "response.txt", "shared raw response", errors)
        if isinstance(provider, dict):
            _validate_provider(provider, prompt, response, "A", 1, errors)
            provider_receipts.append(provider)
            shared_receipt_ids.append(provider.get("receipt_id", ""))
            shared_source_ids[condition] = provider.get("shared_source_id", "")
            shared_invocation_ids[condition] = provider.get("provider_invocation_id", "")
        if isinstance(checkpoint, dict):
            _validate_checkpoint(
                checkpoint,
                "A",
                1,
                response,
                f"shared_sources/{condition}/raw/response.txt",
                errors,
            )
            checkpoint_ids[condition] = checkpoint.get("receipt_id", "")

    trajectory_receipt_ids: list[str] = []
    scores: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    for trajectory_id in TRAJECTORY_ORDER:
        directory = trajectory_root / trajectory_id
        _require_names(
            directory,
            TRAJECTORY_FILES | {f"worker_{worker}" for worker in WORKERS},
            f"trajectory/{trajectory_id}",
            errors,
        )
        config = _read_json(directory / "config.json", "trajectory config", errors)
        score = _read_json(directory / "trajectory_score.json", "trajectory score", errors)
        result = _read_json(directory / "trajectory_result.json", "trajectory result", errors)
        reference = _read_json(
            directory / "worker_A_shared_source_ref.json", "shared-source reference", errors
        )
        manifest = _read_json(directory / "trajectory_receipt.json", "trajectory receipt", errors)
        if isinstance(config, dict):
            _validate_trajectory_config(config, pilot_id, run_id, trajectory_id, errors)
        if isinstance(score, dict):
            try:
                schemas.G1TrajectoryScore.from_json(score)
            except (TypeError, ValueError) as exc:
                errors.append(f"{trajectory_id} score: {exc}")
            scores.append(score)
        if isinstance(result, dict):
            _validate_trajectory_result(result, trajectory_id, errors)
        if isinstance(reference, dict):
            references.append(reference)
        for worker in WORKERS:
            worker_dir = directory / f"worker_{worker}"
            _require_names(worker_dir, WORKER_FILES | {"raw"}, f"{trajectory_id}/worker_{worker}", errors)
            raw = worker_dir / "raw"
            _require_names(raw, RAW_FILES, f"{trajectory_id}/worker_{worker}/raw", errors)
            provider = _read_json(worker_dir / "provider_call_receipt.json", "worker provider receipt", errors)
            checkpoint = _read_json(worker_dir / "checkpoint_receipt.json", "worker checkpoint receipt", errors)
            prompt = _read_text(raw / "prompt.txt", "worker raw prompt", errors)
            response = _read_text(raw / "response.txt", "worker raw response", errors)
            if isinstance(provider, dict):
                _validate_provider(provider, prompt, response, worker, WORKER_STAGES[worker], errors)
                if provider.get("trajectory_id") != trajectory_id:
                    errors.append(f"{trajectory_id}/{worker}: provider trajectory_id mismatch")
                provider_receipts.append(provider)
            if isinstance(checkpoint, dict):
                _validate_checkpoint(
                    checkpoint,
                    worker,
                    WORKER_STAGES[worker],
                    response,
                    f"trajectories/{trajectory_id}/worker_{worker}/raw/response.txt",
                    errors,
                )
        if isinstance(manifest, dict):
            _validate_trajectory_manifest(directory, trajectory_id, manifest, errors)
            trajectory_receipt_ids.append(manifest.get("checksum", ""))

    _validate_references(
        references,
        shared_source_ids,
        shared_invocation_ids,
        shared_receipt_ids,
        checkpoint_ids,
        pilot_accounting,
        errors,
    )
    _validate_scores(pilot_scores, scores, pilot_id, errors)
    _validate_provider_identity(provider_receipts, errors)
    _crosscheck_accounting(pilot_accounting, provider_receipts, errors)
    _validate_pilot_artifact_hashes(root, pilot_config, errors)
    _validate_pilot_receipt(
        root,
        pilot_receipt,
        pilot_id,
        run_id,
        provider_receipts,
        shared_receipt_ids,
        trajectory_receipt_ids,
        errors,
    )
    return errors


def _write_packet(root: Path, packet: EvidencePacketInput) -> None:
    (root / "shared_sources").mkdir()
    (root / "trajectories").mkdir()
    _write_json(root / "pilot_accounting.json", packet.pilot_accounting)
    _write_json(root / "pilot_scores.json", packet.pilot_scores)
    _write_json(root / "pilot_result.json", packet.pilot_result)
    _write_json(root / "pricing_catalog.json", packet.pricing_catalog)
    (root / "provider_selection.md").write_text(packet.provider_selection_md, encoding="utf-8")

    for condition in CONDITIONS:
        source = packet.shared_sources[condition]
        directory = root / "shared_sources" / condition
        raw = directory / "raw"
        raw.mkdir(parents=True)
        _write_json(directory / "provider_call_receipt.json", source["provider_receipt"])
        _write_json(directory / "checkpoint_receipt.json", source["checkpoint_receipt"])
        (raw / "prompt.txt").write_text(source["raw_prompt"], encoding="utf-8")
        (raw / "response.txt").write_text(source["raw_response"], encoding="utf-8")

    trajectory_checksums: list[str] = []
    for trajectory_id in TRAJECTORY_ORDER:
        trajectory = packet.trajectories[trajectory_id]
        directory = root / "trajectories" / trajectory_id
        directory.mkdir(parents=True)
        _write_json(directory / "config.json", trajectory["config"])
        _write_json(directory / "worker_A_shared_source_ref.json", trajectory["worker_A_shared_source_ref"])
        _write_json(directory / "trajectory_score.json", trajectory["score"])
        _write_json(directory / "trajectory_result.json", trajectory["result"])
        for worker in WORKERS:
            data = trajectory["workers"][worker]
            worker_dir = directory / f"worker_{worker}"
            raw = worker_dir / "raw"
            raw.mkdir(parents=True)
            _write_json(worker_dir / "provider_call_receipt.json", data["provider_receipt"])
            _write_json(worker_dir / "checkpoint_receipt.json", data["checkpoint_receipt"])
            (raw / "prompt.txt").write_text(data["raw_prompt"], encoding="utf-8")
            (raw / "response.txt").write_text(data["raw_response"], encoding="utf-8")
        manifest = {
            "trajectory_id": trajectory_id,
            "file_sha256": _trajectory_file_hashes(directory),
            "checksum": "",
        }
        manifest["checksum"] = hashing.compute_manifest_hash(manifest)
        _write_json(directory / "trajectory_receipt.json", manifest)
        trajectory_checksums.append(manifest["checksum"])

    config = dict(packet.pilot_config)
    config["artifact_sha256"] = _pilot_artifact_hashes(root)
    _write_json(root / "pilot_config.json", config)

    providers = _ordered_provider_receipts(packet)
    shared_ids = [
        packet.shared_sources[condition]["provider_receipt"]["receipt_id"]
        for condition in CONDITIONS
    ]
    pilot_receipt = {
        "pilot_id": packet.pilot_id,
        "run_id": packet.run_id,
        "contract_sha256": CONTRACT_SHA256,
        "g0_base_commit": g0_manifest.G0_BASE_COMMIT,
        "g0_manifest_sha256": _g0_manifest_hash(),
        "pilot_config_sha256": _file_hash(root / "pilot_config.json"),
        "trajectory_ids": list(TRAJECTORY_ORDER),
        "provider_receipt_ids": [provider["receipt_id"] for provider in providers],
        "shared_source_receipt_ids": shared_ids,
        "trajectory_receipt_ids": trajectory_checksums,
        "checksum": "",
    }
    pilot_receipt["checksum"] = hashing.compute_manifest_hash(pilot_receipt)
    _write_json(root / "pilot_receipt.json", pilot_receipt)


def _validate_packet_input(packet: EvidencePacketInput) -> list[str]:
    errors: list[str] = []
    if not isinstance(packet.run_id, str) or not packet.run_id:
        errors.append("run_id must be a non-empty string")
    if not isinstance(packet.shared_sources, dict):
        errors.append("shared_sources must be a dict")
        return errors
    if not isinstance(packet.trajectories, dict):
        errors.append("trajectories must be a dict")
        return errors
    if set(packet.shared_sources) != set(CONDITIONS):
        errors.append("shared_sources must contain exactly clean and drift")
    if set(packet.trajectories) != set(TRAJECTORY_ORDER):
        errors.append("trajectories must contain the frozen six trajectory IDs")
    _validate_pilot_config(packet.pilot_config, errors, allow_artifact_hashes=False)
    _validate_pilot_accounting(packet.pilot_accounting, errors)
    _validate_pilot_result(packet.pilot_result, packet.pilot_id, errors)
    _validate_pricing_catalog(packet.pricing_catalog, errors)
    if packet.pilot_config.get("pilot_id") != packet.pilot_id:
        errors.append("pilot_config pilot_id mismatch")
    if packet.pilot_config.get("run_id") != packet.run_id:
        errors.append("pilot_config run_id mismatch")
    if not isinstance(packet.provider_selection_md, str) or not packet.provider_selection_md.strip():
        errors.append("provider_selection_md must be non-empty Markdown")

    receipt_ids: set[str] = set()
    invocation_ids: set[str] = set()
    shared_ids: dict[str, str] = {}
    shared_invocation_ids: dict[str, str] = {}
    shared_receipts: dict[str, str] = {}
    checkpoints: dict[str, str] = {}
    for condition in CONDITIONS:
        source = packet.shared_sources.get(condition)
        if not isinstance(source, dict):
            errors.append(f"shared source {condition} must be a dict")
            continue
        _require_mapping_keys(
            source,
            {"shared_source_id", "provider_receipt", "checkpoint_receipt", "raw_prompt", "raw_response"},
            f"shared source {condition}",
            errors,
        )
        provider = source.get("provider_receipt")
        checkpoint = source.get("checkpoint_receipt")
        prompt = source.get("raw_prompt")
        response = source.get("raw_response")
        if isinstance(provider, dict) and isinstance(prompt, str) and prompt and isinstance(response, str) and response:
            _validate_provider(provider, prompt, response, "A", 1, errors)
            _record_provider_identity(provider, receipt_ids, invocation_ids, errors)
            shared_ids[condition] = source.get("shared_source_id", "")
            shared_receipts[condition] = provider.get("receipt_id", "")
            shared_invocation_ids[condition] = provider.get("provider_invocation_id", "")
            if provider.get("shared_source_id") != source.get("shared_source_id"):
                errors.append(f"shared source {condition}: provider shared_source_id mismatch")
        else:
            errors.append(f"shared source {condition}: invalid provider/raw content")
        if isinstance(checkpoint, dict) and isinstance(response, str) and response:
            _validate_checkpoint(
                checkpoint,
                "A",
                1,
                response,
                f"shared_sources/{condition}/raw/response.txt",
                errors,
            )
            checkpoints[condition] = checkpoint.get("receipt_id", "")
        else:
            errors.append(f"shared source {condition}: invalid checkpoint")

    scores: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    for trajectory_id in TRAJECTORY_ORDER:
        trajectory = packet.trajectories.get(trajectory_id)
        if not isinstance(trajectory, dict):
            errors.append(f"{trajectory_id}: trajectory must be a dict")
            continue
        _require_mapping_keys(
            trajectory,
            {"config", "score", "result", "worker_A_shared_source_ref", "workers"},
            trajectory_id,
            errors,
        )
        config = trajectory.get("config")
        score = trajectory.get("score")
        result = trajectory.get("result")
        reference = trajectory.get("worker_A_shared_source_ref")
        if isinstance(config, dict):
            _validate_trajectory_config(config, packet.pilot_id, packet.run_id, trajectory_id, errors)
        else:
            errors.append(f"{trajectory_id}: invalid config")
        if isinstance(score, dict):
            try:
                schemas.G1TrajectoryScore.from_json(score)
            except (TypeError, ValueError) as exc:
                errors.append(f"{trajectory_id} score: {exc}")
            scores.append(score)
        else:
            errors.append(f"{trajectory_id}: invalid score")
        if isinstance(result, dict):
            _validate_trajectory_result(result, trajectory_id, errors)
        else:
            errors.append(f"{trajectory_id}: invalid result")
        if isinstance(reference, dict):
            references.append(reference)
        else:
            errors.append(f"{trajectory_id}: invalid Worker-A reference")
        workers = trajectory.get("workers")
        if not isinstance(workers, dict) or set(workers) != set(WORKERS):
            errors.append(f"{trajectory_id}: workers must be exactly B, C, D")
            continue
        for worker in WORKERS:
            data = workers[worker]
            if not isinstance(data, dict):
                errors.append(f"{trajectory_id}/{worker}: worker data must be a dict")
                continue
            _require_mapping_keys(
                data,
                {"provider_receipt", "checkpoint_receipt", "raw_prompt", "raw_response"},
                f"{trajectory_id}/{worker}",
                errors,
            )
            provider = data.get("provider_receipt")
            checkpoint = data.get("checkpoint_receipt")
            prompt = data.get("raw_prompt")
            response = data.get("raw_response")
            if isinstance(provider, dict) and isinstance(prompt, str) and prompt and isinstance(response, str) and response:
                _validate_provider(provider, prompt, response, worker, WORKER_STAGES[worker], errors)
                if provider.get("trajectory_id") != trajectory_id:
                    errors.append(f"{trajectory_id}/{worker}: provider trajectory_id mismatch")
                _record_provider_identity(provider, receipt_ids, invocation_ids, errors)
            else:
                errors.append(f"{trajectory_id}/{worker}: invalid provider/raw content")
            if isinstance(checkpoint, dict) and isinstance(response, str) and response:
                _validate_checkpoint(
                    checkpoint,
                    worker,
                    WORKER_STAGES[worker],
                    response,
                    f"trajectories/{trajectory_id}/worker_{worker}/raw/response.txt",
                    errors,
                )
            else:
                errors.append(f"{trajectory_id}/{worker}: invalid checkpoint")

    _validate_references(
        references,
        shared_ids,
        shared_invocation_ids,
        [shared_receipts.get(condition, "") for condition in CONDITIONS],
        checkpoints,
        packet.pilot_accounting,
        errors,
    )
    _validate_scores(packet.pilot_scores, scores, packet.pilot_id, errors)
    return errors


def _validate_provider(
    receipt: dict[str, Any], prompt: str, response: str, worker: str, stage: int, errors: list[str]
) -> None:
    if not prompt:
        errors.append(f"provider receipt raw prompt is empty for {worker}/{stage}")
    if not response:
        errors.append(f"provider receipt raw response is empty for {worker}/{stage}")
    try:
        schemas.ProviderCallReceipt.from_json(receipt)
    except (TypeError, ValueError) as exc:
        errors.append(f"provider receipt: {exc}")
    if receipt.get("worker_label") != worker or receipt.get("stage") != stage:
        errors.append(f"provider receipt worker/stage mismatch for {worker}/{stage}")
    if receipt.get("raw_prompt_sha256") != hashing.compute_sha256(prompt):
        errors.append(f"provider receipt raw prompt hash mismatch for {worker}/{stage}")
    response_hash = hashing.compute_sha256(response)
    if receipt.get("raw_response_sha256") != response_hash:
        errors.append(f"provider receipt raw response hash mismatch for {worker}/{stage}")
    if receipt.get("response_sha256") != response_hash:
        errors.append(f"provider receipt response hash mismatch for {worker}/{stage}")


def _validate_checkpoint(
    receipt: dict[str, Any],
    worker: str,
    stage: int,
    response: str,
    expected_artifact_path: str,
    errors: list[str],
) -> None:
    expected = {field.name for field in dataclasses.fields(g0_schemas.CheckpointReceipt)}
    if set(receipt) != expected:
        errors.append(f"checkpoint {worker}/{stage}: fields must be exactly {sorted(expected)}")
        return
    try:
        parsed = g0_schemas.CheckpointReceipt.from_json(receipt)
    except (TypeError, ValueError) as exc:
        errors.append(f"checkpoint {worker}/{stage}: {exc}")
        return
    if not isinstance(parsed.receipt_id, str) or not SHA256_RE.fullmatch(parsed.receipt_id):
        errors.append(f"checkpoint {worker}/{stage}: receipt_id must be 64-char hex")
    elif parsed.receipt_id != hashing.compute_receipt_hash(receipt):
        errors.append(f"checkpoint {worker}/{stage}: receipt_id hash mismatch")
    if parsed.stage_number != stage or parsed.worker_label != worker:
        errors.append(f"checkpoint {worker}/{stage}: worker/stage mismatch")
    if not isinstance(parsed.stage_number, int) or isinstance(parsed.stage_number, bool):
        errors.append(f"checkpoint {worker}/{stage}: stage_number must be int")
    if not isinstance(parsed.worker_label, str) or not parsed.worker_label:
        errors.append(f"checkpoint {worker}/{stage}: worker_label must be non-empty string")
    if not isinstance(parsed.artifact_path, str):
        errors.append(f"checkpoint {worker}/{stage}: artifact_path must be string")
    if parsed.artifact_path != expected_artifact_path:
        errors.append(f"checkpoint {worker}/{stage}: artifact_path mismatch")
    if not isinstance(parsed.artifact_sha256, str) or not SHA256_RE.fullmatch(parsed.artifact_sha256):
        errors.append(f"checkpoint {worker}/{stage}: artifact_sha256 must be 64-char hex")
    elif parsed.artifact_sha256 != hashing.compute_sha256(response):
        errors.append(f"checkpoint {worker}/{stage}: artifact hash mismatch")
    if not isinstance(parsed.test_results, dict) or not parsed.test_results or not all(
        isinstance(key, str) and isinstance(value, bool) for key, value in parsed.test_results.items()
    ):
        errors.append(f"checkpoint {worker}/{stage}: invalid test_results")
    if not isinstance(parsed.timestamp, (int, float)) or isinstance(parsed.timestamp, bool):
        errors.append(f"checkpoint {worker}/{stage}: invalid timestamp")
    if not isinstance(parsed.session_token_count, int) or isinstance(parsed.session_token_count, bool) or parsed.session_token_count < 0:
        errors.append(f"checkpoint {worker}/{stage}: invalid session_token_count")


def _validate_trajectory_manifest(
    directory: Path, trajectory_id: str, manifest: dict[str, Any], errors: list[str]
) -> None:
    if set(manifest) != {"trajectory_id", "file_sha256", "checksum"}:
        errors.append(f"{trajectory_id}: invalid trajectory receipt fields")
        return
    if manifest.get("trajectory_id") != trajectory_id:
        errors.append(f"{trajectory_id}: trajectory receipt ID mismatch")
    try:
        expected = _trajectory_file_hashes(directory)
    except OSError as exc:
        errors.append(f"{trajectory_id}: cannot hash trajectory files: {exc}")
        return
    if manifest.get("file_sha256") != expected:
        errors.append(f"{trajectory_id}: trajectory receipt file hashes mismatch")
    checksum = manifest.get("checksum")
    if not isinstance(checksum, str) or checksum != hashing.compute_manifest_hash(manifest):
        errors.append(f"{trajectory_id}: trajectory receipt checksum mismatch")


def _validate_pilot_receipt(
    root: Path,
    receipt: Any,
    pilot_id: Any,
    run_id: Any,
    providers: list[dict[str, Any]],
    shared_ids: list[str],
    trajectory_receipt_ids: list[str],
    errors: list[str],
) -> None:
    if not isinstance(receipt, dict):
        return
    try:
        schemas.G1PilotEvidence.from_json(receipt)
    except (TypeError, ValueError) as exc:
        errors.append(f"pilot receipt: {exc}")
    expected_provider_ids = [provider.get("receipt_id", "") for provider in providers]
    checks = {
        "pilot_id": pilot_id,
        "run_id": run_id,
        "contract_sha256": CONTRACT_SHA256,
        "g0_base_commit": g0_manifest.G0_BASE_COMMIT,
        "g0_manifest_sha256": _g0_manifest_hash(),
        "pilot_config_sha256": _file_hash(root / "pilot_config.json"),
        "trajectory_ids": list(TRAJECTORY_ORDER),
        "provider_receipt_ids": expected_provider_ids,
        "shared_source_receipt_ids": shared_ids,
        "trajectory_receipt_ids": trajectory_receipt_ids,
    }
    for key, value in checks.items():
        if receipt.get(key) != value:
            errors.append(f"pilot receipt {key} mismatch")


def _validate_pilot_config(config: Any, errors: list[str], allow_artifact_hashes: bool = True) -> None:
    required = {
        "pilot_id", "run_id", "contract_sha256", "g0_base_commit", "synthetic",
        "task", "conditions", "architectures",
    }
    allowed = required | ({"artifact_sha256"} if allow_artifact_hashes else set())
    if not isinstance(config, dict) or set(config) != allowed:
        errors.append(f"pilot_config fields must be exactly {sorted(allowed)}")
        return
    if not isinstance(config.get("pilot_id"), str) or not config.get("pilot_id"):
        errors.append("pilot_config pilot_id invalid")
    if not isinstance(config.get("run_id"), str) or not config.get("run_id"):
        errors.append("pilot_config run_id invalid")
    if config.get("contract_sha256") != CONTRACT_SHA256:
        errors.append("pilot_config contract hash mismatch")
    if config.get("g0_base_commit") != g0_manifest.G0_BASE_COMMIT:
        errors.append("pilot_config G0 base mismatch")
    if config.get("synthetic") is not True or config.get("task") != "A":
        errors.append("pilot_config synthetic/task fields invalid")
    if config.get("conditions") != list(CONDITIONS) or config.get("architectures") != list(ARCHITECTURES):
        errors.append("pilot_config topology mismatch")
    if allow_artifact_hashes and not isinstance(config.get("artifact_sha256"), dict):
        errors.append("pilot_config artifact_sha256 must be a dict")


def _validate_trajectory_config(
    config: dict[str, Any], pilot_id: Any, run_id: Any, trajectory_id: str, errors: list[str]
) -> None:
    required = {"pilot_id", "run_id", "trajectory_id", "task", "condition", "architecture", "synthetic"}
    if set(config) != required:
        errors.append(f"{trajectory_id}: invalid trajectory config fields")
        return
    _, condition, architecture = trajectory_id.split("-", 2)
    expected = {
        "pilot_id": pilot_id,
        "run_id": run_id,
        "trajectory_id": trajectory_id,
        "task": "A",
        "condition": condition,
        "architecture": architecture,
        "synthetic": True,
    }
    if config != expected:
        errors.append(f"{trajectory_id}: trajectory config mismatch")


def _validate_trajectory_result(result: Any, trajectory_id: str, errors: list[str]) -> None:
    required = {
        "trajectory_id", "execution_status", "integrity_valid", "contamination_detected",
        "accounting_valid", "task_score_valid", "warnings",
    }
    if not isinstance(result, dict) or set(result) != required:
        errors.append(f"{trajectory_id}: invalid trajectory result fields")
        return
    if result.get("trajectory_id") != trajectory_id or result.get("execution_status") not in RESULT_STATUSES:
        errors.append(f"{trajectory_id}: invalid trajectory result identity/status")
    for key in ("integrity_valid", "contamination_detected", "accounting_valid", "task_score_valid"):
        if not isinstance(result.get(key), bool):
            errors.append(f"{trajectory_id}: {key} must be bool")
    if not isinstance(result.get("warnings"), list) or any(not isinstance(item, str) for item in result.get("warnings", [])):
        errors.append(f"{trajectory_id}: warnings must be a string list")


def _validate_pilot_result(result: Any, pilot_id: Any, errors: list[str]) -> None:
    required = {
        "pilot_id", "execution_status", "integrity_valid", "contamination_detected",
        "accounting_valid", "task_score_valid", "warnings",
    }
    if not isinstance(result, dict) or set(result) != required:
        errors.append("invalid pilot_result fields")
        return
    if result.get("pilot_id") != pilot_id or result.get("execution_status") not in RESULT_STATUSES:
        errors.append("pilot_result identity/status mismatch")
    for key in ("integrity_valid", "contamination_detected", "accounting_valid", "task_score_valid"):
        if not isinstance(result.get(key), bool):
            errors.append(f"pilot_result {key} must be bool")
    if not isinstance(result.get("warnings"), list) or any(not isinstance(item, str) for item in result.get("warnings", [])):
        errors.append("pilot_result warnings must be a string list")


def _validate_scores(pilot_scores: Any, scores: list[dict[str, Any]], pilot_id: Any, errors: list[str]) -> None:
    if not isinstance(pilot_scores, dict) or set(pilot_scores) != {"pilot_id", "trajectory_scores"}:
        errors.append("pilot_scores fields invalid")
        return
    if pilot_scores.get("pilot_id") != pilot_id or pilot_scores.get("trajectory_scores") != scores:
        errors.append("pilot_scores content mismatch")
    ids = [score.get("trajectory_id") for score in scores]
    if ids != list(TRAJECTORY_ORDER):
        errors.append("trajectory score ordering/IDs mismatch")


def _validate_pricing_catalog(catalog: Any, errors: list[str]) -> None:
    if not isinstance(catalog, dict) or set(catalog) != {"synthetic", "entries"} or catalog.get("synthetic") is not True:
        errors.append("pricing_catalog fields invalid")
        return
    entries = catalog.get("entries")
    if not isinstance(entries, list) or len(entries) != 3:
        errors.append("pricing_catalog must contain exactly three entries")
        return
    categories = []
    for entry in entries:
        required = {"provider", "model", "category", "price_per_million", "effective_date", "source"}
        if not isinstance(entry, dict) or set(entry) != required:
            errors.append("pricing entry fields invalid")
            continue
        categories.append(entry.get("category"))
        if not all(isinstance(entry.get(key), str) and entry.get(key) for key in ("provider", "model", "category", "effective_date", "source")):
            errors.append("pricing entry strings invalid")
        effective_date = entry.get("effective_date")
        if isinstance(effective_date, str) and not RFC3339_RE.fullmatch(effective_date):
            errors.append("pricing entry effective_date must be RFC 3339")
        price = entry.get("price_per_million")
        if not isinstance(price, int) or isinstance(price, bool) or price < 0:
            errors.append("pricing entry price invalid")
    if set(categories) != {"uncached_input", "cached_input", "output"}:
        errors.append("pricing categories invalid")


def _validate_pilot_accounting(data: Any, errors: list[str]) -> None:
    required = {
        "accounting_valid", "physical_invocation_ids", "physical_calculated_costs",
        "total_calculated_pilot_cost", "logical_trajectories", "shared_allocations",
    }
    if not isinstance(data, dict) or set(data) != required:
        errors.append("pilot_accounting fields invalid")
        return
    ids = data.get("physical_invocation_ids")
    costs = data.get("physical_calculated_costs")
    logical = data.get("logical_trajectories")
    allocations = data.get("shared_allocations")
    if data.get("accounting_valid") is not True:
        errors.append("pilot_accounting accounting_valid must be true")
    if not isinstance(ids, list) or len(ids) != 20 or len(set(ids)) != 20 or any(not isinstance(item, str) or not item for item in ids):
        errors.append("pilot_accounting must contain 20 unique invocation IDs")
        ids = []
    if not isinstance(costs, dict) or set(costs) != set(ids) or any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in costs.values()
    ):
        errors.append("pilot_accounting physical costs invalid")
        costs = {}
    total = data.get("total_calculated_pilot_cost")
    if not isinstance(total, int) or isinstance(total, bool) or total < 0 or total != sum(costs.values()):
        errors.append("pilot_accounting physical total mismatch")
    if not isinstance(logical, list) or len(logical) != 6:
        errors.append("pilot_accounting must contain six logical trajectories")
        logical = []
    logical_total = 0
    logical_ids = []
    for item in logical:
        required_item = {
            "trajectory_id", "successor_call_ids", "successor_calculated_cost",
            "allocated_shared_source_cost", "logical_trajectory_cost",
        }
        if not isinstance(item, dict) or set(item) != required_item:
            errors.append("logical trajectory accounting fields invalid")
            continue
        logical_ids.append(item.get("trajectory_id"))
        successor_ids = item.get("successor_call_ids")
        if (
            not isinstance(successor_ids, list)
            or len(successor_ids) != 3
            or any(not isinstance(value, str) or not value for value in successor_ids)
            or len(set(successor_ids)) != 3
        ):
            errors.append("logical trajectory successor IDs invalid")
            continue
        try:
            successor_total = sum(costs[invocation_id] for invocation_id in successor_ids)
        except KeyError:
            errors.append("logical trajectory references unknown invocation")
            continue
        allocated = item.get("allocated_shared_source_cost")
        successor_cost = item.get("successor_calculated_cost")
        logical_cost = item.get("logical_trajectory_cost")
        if not isinstance(allocated, int) or isinstance(allocated, bool) or allocated < 0:
            errors.append("logical trajectory allocation invalid")
            continue
        if not isinstance(successor_cost, int) or isinstance(successor_cost, bool) or successor_cost < 0:
            errors.append("logical trajectory successor cost invalid")
            continue
        if not isinstance(logical_cost, int) or isinstance(logical_cost, bool) or logical_cost < 0:
            errors.append("logical trajectory cost invalid")
            continue
        if successor_cost != successor_total or logical_cost != successor_total + allocated:
            errors.append("logical trajectory cost mismatch")
            continue
        logical_total += logical_cost
    if logical_ids != list(TRAJECTORY_ORDER):
        errors.append("logical trajectory ordering/IDs mismatch")
    if isinstance(total, int) and not isinstance(total, bool) and logical_total != total:
        errors.append("logical and physical pilot totals differ")
    if not isinstance(allocations, list) or len(allocations) != 2:
        errors.append("pilot_accounting must contain two shared allocations")
        return
    for condition, allocation in zip(CONDITIONS, allocations):
        required_allocation = {"shared_source_id", "physical_calculated_cost", "allocations", "sum_allocated"}
        if not isinstance(allocation, dict) or set(allocation) != required_allocation:
            errors.append("shared allocation fields invalid")
            continue
        source_id = allocation.get("shared_source_id")
        if not isinstance(source_id, str) or not source_id:
            errors.append("shared allocation source ID invalid")
        shares = allocation.get("allocations")
        physical = allocation.get("physical_calculated_cost")
        if not isinstance(physical, int) or isinstance(physical, bool) or physical < 0:
            errors.append("shared allocation physical cost invalid")
            continue
        if not isinstance(shares, dict) or list(shares) != list(ARCHITECTURES):
            errors.append("shared allocation order invalid")
            continue
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in shares.values()):
            errors.append("shared allocation values invalid")
            continue
        sum_allocated = allocation.get("sum_allocated")
        if not isinstance(sum_allocated, int) or isinstance(sum_allocated, bool) or sum_allocated < 0:
            errors.append("shared allocation sum invalid")
            continue
        if sum_allocated != sum(shares.values()) or sum_allocated != physical:
            errors.append("shared allocation sum mismatch")


def _crosscheck_accounting(data: Any, providers: list[dict[str, Any]], errors: list[str]) -> None:
    if not isinstance(data, dict):
        return
    provider_ids = [provider.get("provider_invocation_id") for provider in providers]
    if data.get("physical_invocation_ids") != provider_ids:
        errors.append("pilot_accounting invocation IDs do not match provider receipts")
    receipt_costs = {
        provider.get("provider_invocation_id"): provider.get("calculated_micro_usd_cost")
        for provider in providers
    }
    if data.get("physical_calculated_costs") != receipt_costs:
        errors.append("pilot_accounting costs do not match provider receipts")
    allocations = data.get("shared_allocations")
    if isinstance(allocations, list) and len(allocations) == 2 and len(providers) >= 2:
        for condition, allocation, provider in zip(CONDITIONS, allocations, providers[:2]):
            if not isinstance(allocation, dict):
                continue
            if allocation.get("shared_source_id") != provider.get("shared_source_id"):
                errors.append(f"{condition}: allocation shared_source_id does not match provider")
            if allocation.get("physical_calculated_cost") != provider.get("calculated_micro_usd_cost"):
                errors.append(f"{condition}: allocation physical cost does not match provider")


def _validate_references(
    references: list[dict[str, Any]],
    shared_source_ids: dict[str, str],
    shared_invocation_ids: dict[str, str],
    shared_receipt_ids: list[str],
    checkpoint_ids: dict[str, str],
    pilot_accounting: Any,
    errors: list[str],
) -> None:
    if len(references) != 6:
        errors.append("exactly six Worker-A references are required")
        return
    receipt_by_condition = dict(zip(CONDITIONS, shared_receipt_ids))
    allocation_by_condition: dict[str, dict[str, Any]] = {}
    if isinstance(pilot_accounting, dict):
        allocations = pilot_accounting.get("shared_allocations")
        if isinstance(allocations, list):
            allocation_by_condition = {
                condition: allocation
                for condition, allocation in zip(CONDITIONS, allocations)
                if isinstance(allocation, dict)
            }
    counts = {condition: 0 for condition in CONDITIONS}
    for trajectory_id, reference in zip(TRAJECTORY_ORDER, references):
        required = {
            "shared_source_id", "provider_invocation_id", "provider_receipt_id",
            "checkpoint_receipt_id", "condition", "allocated_shared_source_cost",
        }
        if not isinstance(reference, dict) or set(reference) != required:
            errors.append(f"{trajectory_id}: Worker-A reference fields invalid")
            continue
        _, expected_condition, _ = trajectory_id.split("-", 2)
        condition = reference.get("condition")
        if condition != expected_condition:
            errors.append(f"{trajectory_id}: Worker-A reference condition mismatch")
            continue
        counts[condition] += 1
        if reference.get("shared_source_id") != shared_source_ids.get(condition):
            errors.append(f"{trajectory_id}: shared_source_id mismatch")
        if reference.get("provider_receipt_id") != receipt_by_condition.get(condition):
            errors.append(f"{trajectory_id}: shared provider receipt ID mismatch")
        if reference.get("checkpoint_receipt_id") != checkpoint_ids.get(condition):
            errors.append(f"{trajectory_id}: shared checkpoint receipt ID mismatch")
        if reference.get("provider_invocation_id") != shared_invocation_ids.get(condition):
            errors.append(f"{trajectory_id}: shared provider invocation ID mismatch")
        allocation = reference.get("allocated_shared_source_cost")
        if not isinstance(allocation, int) or isinstance(allocation, bool) or allocation < 0:
            errors.append(f"{trajectory_id}: allocated shared-source cost invalid")
        else:
            architecture = trajectory_id.split("-", 2)[2]
            expected_allocation = allocation_by_condition.get(condition, {}).get("allocations", {}).get(architecture)
            if allocation != expected_allocation:
                errors.append(f"{trajectory_id}: allocated shared-source cost mismatch")
    if counts != {"clean": 3, "drift": 3}:
        errors.append("Worker-A reference counts must be three per condition")


def _validate_provider_identity(providers: list[dict[str, Any]], errors: list[str]) -> None:
    if len(providers) != 20:
        errors.append("exactly 20 provider receipts are required")
    receipt_ids: set[str] = set()
    invocation_ids: set[str] = set()
    for provider in providers:
        _record_provider_identity(provider, receipt_ids, invocation_ids, errors)


def _record_provider_identity(
    provider: dict[str, Any], receipt_ids: set[str], invocation_ids: set[str], errors: list[str]
) -> None:
    receipt_id = provider.get("receipt_id")
    invocation_id = provider.get("provider_invocation_id")
    if receipt_id in receipt_ids:
        errors.append("duplicate provider receipt_id")
    if invocation_id in invocation_ids:
        errors.append("duplicate physical provider_invocation_id")
    if isinstance(receipt_id, str):
        receipt_ids.add(receipt_id)
    if isinstance(invocation_id, str):
        invocation_ids.add(invocation_id)


def _validate_pilot_artifact_hashes(root: Path, config: dict[str, Any], errors: list[str]) -> None:
    try:
        expected = _pilot_artifact_hashes(root)
    except OSError as exc:
        errors.append(f"cannot hash pilot artifacts: {exc}")
        return
    if config.get("artifact_sha256") != expected:
        errors.append("pilot_config artifact hashes mismatch")


def _trajectory_file_hashes(directory: Path) -> dict[str, str]:
    files = [
        "config.json", "worker_A_shared_source_ref.json", "trajectory_score.json",
        "trajectory_result.json",
    ]
    for worker in WORKERS:
        files.extend([
            f"worker_{worker}/provider_call_receipt.json",
            f"worker_{worker}/checkpoint_receipt.json",
            f"worker_{worker}/raw/prompt.txt",
            f"worker_{worker}/raw/response.txt",
        ])
    return {name: _file_hash(directory / name) for name in sorted(files)}


def _pilot_artifact_hashes(root: Path) -> dict[str, str]:
    files = [
        "pilot_accounting.json", "pilot_scores.json", "pilot_result.json",
        "pricing_catalog.json", "provider_selection.md",
    ]
    for condition in CONDITIONS:
        files.extend([
            f"shared_sources/{condition}/provider_call_receipt.json",
            f"shared_sources/{condition}/checkpoint_receipt.json",
            f"shared_sources/{condition}/raw/prompt.txt",
            f"shared_sources/{condition}/raw/response.txt",
        ])
    return {name: _file_hash(root / name) for name in sorted(files)}


def _ordered_provider_receipts(packet: EvidencePacketInput) -> list[dict[str, Any]]:
    receipts = [packet.shared_sources[condition]["provider_receipt"] for condition in CONDITIONS]
    for trajectory_id in TRAJECTORY_ORDER:
        for worker in WORKERS:
            receipts.append(packet.trajectories[trajectory_id]["workers"][worker]["provider_receipt"])
    return receipts


def _g0_manifest_hash() -> str:
    return hashing.compute_sha256(serialization.canonical_json(g0_manifest.FROZEN_MANIFEST))


def _validate_target(run_root: Path, pilot_id: str) -> list[str]:
    errors: list[str] = []
    if run_root.exists() and (not run_root.is_dir() or run_root.is_symlink()):
        errors.append("run_root must be a real directory")
        return errors
    resolved_root = run_root.resolve(strict=False)
    target = (resolved_root / pilot_id).resolve(strict=False)
    if not target.is_relative_to(resolved_root):
        errors.append("pilot path escapes run_root")
    if target.exists() or target.is_symlink():
        errors.append("pilot destination already exists")
    return errors


def _validate_component(value: Any, label: str) -> list[str]:
    if not isinstance(value, str) or not value:
        return [f"{label} must be a non-empty string"]
    if value in {".", ".."} or "/" in value or "\\" in value or Path(value).is_absolute():
        return [f"{label} is not a safe path component"]
    return []


def _require_names(directory: Path, expected: frozenset[str] | set[str], label: str, errors: list[str]) -> None:
    if not directory.is_dir() or directory.is_symlink():
        errors.append(f"missing directory: {label}")
        return
    children = list(directory.iterdir())
    actual = {path.name for path in children}
    for path in children:
        if path.is_symlink():
            errors.append(f"{label}: symlink not allowed: {path.name}")
    missing = set(expected) - actual
    unexpected = actual - set(expected)
    for name in sorted(missing):
        errors.append(f"{label}: missing {name}")
    for name in sorted(unexpected):
        errors.append(f"{label}: unexpected {name}")


def _require_mapping_keys(data: dict[str, Any], expected: set[str], label: str, errors: list[str]) -> None:
    if set(data) != expected:
        errors.append(f"{label}: fields must be exactly {sorted(expected)}")


def _read_json(path: Path, label: str, errors: list[str]) -> Any:
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError("path must be a regular non-symlink file")
        raw = path.read_text(encoding="utf-8")
        pairs: list[tuple[str, Any]] = []

        def reject_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in items:
                if key in result:
                    raise ValueError(f"duplicate JSON key: {key}")
                result[key] = value
            return result

        data = json.loads(raw, object_pairs_hook=reject_duplicates, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        canonical = serialization.canonical_json(data) + "\n"
        if raw != canonical:
            errors.append(f"{label}: JSON is not canonical UTF-8")
        return data
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        errors.append(f"{label}: cannot parse JSON: {exc}")
        return None


def _read_text(path: Path, label: str, errors: list[str]) -> str:
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError("path must be a regular non-symlink file")
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        errors.append(f"{label}: cannot read text: {exc}")
        return ""


def _write_json(path: Path, data: Any) -> None:
    path.write_text(serialization.canonical_json(data) + "\n", encoding="utf-8")


def _file_hash(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise OSError(f"not a regular non-symlink file: {path}")
    return hashing.compute_sha256(path.read_bytes())


def _result(packet: EvidencePacketInput, path: Path | None, errors: list[str]) -> EvidencePacketResult:
    shared_sources = packet.shared_sources if isinstance(packet.shared_sources, dict) else {}
    trajectories = packet.trajectories if isinstance(packet.trajectories, dict) else {}
    return EvidencePacketResult(
        packet_valid=not errors,
        packet_path=str(path) if path else None,
        shared_source_count=len(shared_sources),
        trajectory_count=len(trajectories),
        reference_count=sum(
            1 for trajectory in trajectories.values()
            if isinstance(trajectory, dict) and "worker_A_shared_source_ref" in trajectory
        ),
        errors=tuple(errors),
    )
