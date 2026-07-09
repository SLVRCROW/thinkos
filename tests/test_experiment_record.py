"""Tests for the ExperimentRecord schema and store methods."""

import json
import uuid
import pytest
from thinkos.schema.experiment_record import (
    ExperimentRecord,
    validate,
    validate_experiment_id,
    normalize,
    SCHEMA_VERSION,
    VALID_DECISIONS,
)
from thinkos.store.sqlite_store import SQLiteStore, DuplicateError


# ── Helpers ─────────────────────────────────────────────────────────

def _make_record(**overrides) -> ExperimentRecord:
    """Create a valid ExperimentRecord with sensible defaults."""
    fields = {
        "experiment_id": f"exp_{uuid.uuid4()}",
        "session_id": "sess_test",
        "timestamp": "2026-07-09T12:00:00Z",
        "tool_name": "write_file",
        "params_summary": "write_file: path='hello.txt', 15 bytes",
        "metric_name": "file_size_bytes",
        "metric_value": 15.0,
        "decision": "keep",
    }
    fields.update(overrides)
    return ExperimentRecord(**fields)


# ── Schema validation ──────────────────────────────────────────────

class TestExperimentRecordSchema:

    def test_required_fields(self):
        record = _make_record()
        errors = validate(record)
        assert errors == []

    def test_all_fields(self):
        record = _make_record(
            baseline_value=10.0,
            baseline_experiment_id="exp_baseline_uuid",
            decision_reason="File size decreased, keeping change",
            receipt_id="rct_some_uuid",
            packet_ids=["ctx_one", "ctx_two"],
            tags=["write", "experiment"],
            metadata={"author": "test"},
        )
        errors = validate(record)
        assert errors == []

    def test_experiment_id_prefix(self):
        record = _make_record(experiment_id="bad_prefix")
        errors = validate(record)
        assert any("must start with 'exp_'" in e for e in errors)

    def test_experiment_id_uuid(self):
        record = _make_record(experiment_id="exp_not_a_uuid")
        errors = validate(record)
        assert any("not a valid UUID" in e for e in errors)

    def test_session_id_required(self):
        record = _make_record(session_id="")
        errors = validate(record)
        assert any("session_id is required" in e for e in errors)

    def test_timestamp_required(self):
        record = _make_record(timestamp="")
        errors = validate(record)
        assert any("timestamp is required" in e for e in errors)

    def test_tool_name_required(self):
        record = _make_record(tool_name="")
        errors = validate(record)
        assert any("tool_name is required" in e for e in errors)

    def test_metric_name_required(self):
        record = _make_record(metric_name="")
        errors = validate(record)
        assert any("metric_name is required" in e for e in errors)

    def test_metric_value_numeric(self):
        record = _make_record(metric_value="not_a_number")
        errors = validate(record)
        assert any("metric_value must be numeric" in e for e in errors)

    def test_metric_value_rejects_bool(self):
        record = _make_record(metric_value=True)
        errors = validate(record)
        assert any("not bool" in e for e in errors)

    def test_metric_value_accepts_int(self):
        record = _make_record(metric_value=42)
        errors = validate(record)
        assert errors == []

    def test_baseline_value_rejects_bool(self):
        record = _make_record(baseline_value=False)
        errors = validate(record)
        assert any("not bool" in e for e in errors)

    def test_baseline_value_accepts_none(self):
        record = _make_record(baseline_value=None)
        errors = validate(record)
        assert errors == []

    def test_baseline_value_accepts_int(self):
        record = _make_record(baseline_value=10)
        errors = validate(record)
        assert errors == []

    def test_decision_enum(self):
        for d in VALID_DECISIONS:
            record = _make_record(decision=d)
            errors = validate(record)
            assert errors == []

    def test_decision_invalid(self):
        record = _make_record(decision="maybe")
        errors = validate(record)
        assert any("decision must be one of" in e for e in errors)

    def test_params_summary_must_be_str_or_none(self):
        record = _make_record(params_summary={"raw": "dict"})
        errors = validate(record)
        assert any("params_summary must be a string" in e for e in errors)

    def test_params_summary_accepts_none(self):
        record = _make_record(params_summary=None)
        errors = validate(record)
        assert errors == []

    def test_params_summary_accepts_str(self):
        record = _make_record(params_summary="sanitized string")
        errors = validate(record)
        assert errors == []

    def test_receipt_id_must_be_str_or_none(self):
        record = _make_record(receipt_id=123)
        errors = validate(record)
        assert any("receipt_id must be a string" in e for e in errors)

    def test_packet_ids_must_be_list(self):
        record = _make_record(packet_ids="not_a_list")
        errors = validate(record)
        assert any("packet_ids must be a list" in e for e in errors)

    def test_packet_ids_elements_must_be_strings(self):
        record = _make_record(packet_ids=[123, 456])
        errors = validate(record)
        assert any("must be a string" in e for e in errors)

    def test_tags_must_be_list(self):
        record = _make_record(tags="not_a_list")
        errors = validate(record)
        assert any("tags must be a list" in e for e in errors)

    def test_tags_elements_must_be_strings(self):
        record = _make_record(tags=[1, 2, 3])
        errors = validate(record)
        assert any("must be a string" in e for e in errors)

    def test_metadata_must_be_dict(self):
        record = _make_record(metadata="not_a_dict")
        errors = validate(record)
        assert any("metadata must be a dict" in e for e in errors)


# ── Normalization ──────────────────────────────────────────────────

class TestExperimentRecordNormalize:

    def test_metric_value_int_to_float(self):
        record = _make_record(metric_value=42)
        normalize(record)
        assert isinstance(record.metric_value, float)
        assert record.metric_value == 42.0

    def test_metric_value_float_stays_float(self):
        record = _make_record(metric_value=42.5)
        normalize(record)
        assert isinstance(record.metric_value, float)
        assert record.metric_value == 42.5

    def test_baseline_value_int_to_float(self):
        record = _make_record(baseline_value=10)
        normalize(record)
        assert isinstance(record.baseline_value, float)
        assert record.baseline_value == 10.0

    def test_baseline_value_none_stays_none(self):
        record = _make_record(baseline_value=None)
        normalize(record)
        assert record.baseline_value is None


# ── Store methods ──────────────────────────────────────────────────

class TestExperimentRecordStore:

    @pytest.fixture
    def store(self):
        return SQLiteStore(":memory:")

    def test_write_and_read(self, store):
        record = _make_record()
        store.write_experiment(record)
        loaded = store.read_experiment(record.experiment_id)
        assert loaded is not None
        assert loaded.experiment_id == record.experiment_id
        assert loaded.metric_value == record.metric_value
        assert loaded.decision == record.decision

    def test_duplicate_id_rejected(self, store):
        record = _make_record()
        store.write_experiment(record)
        with pytest.raises(DuplicateError):
            store.write_experiment(record)

    def test_read_nonexistent(self, store):
        loaded = store.read_experiment("exp_nonexistent")
        assert loaded is None

    def test_list_by_session(self, store):
        e1 = _make_record(session_id="sess_a", metric_value=1.0)
        e2 = _make_record(session_id="sess_a", metric_value=2.0)
        e3 = _make_record(session_id="sess_b", metric_value=3.0)
        store.write_experiment(e1)
        store.write_experiment(e2)
        store.write_experiment(e3)

        results = store.list_experiments("sess_a")
        assert len(results) == 2
        assert results[0].experiment_id == e1.experiment_id
        assert results[1].experiment_id == e2.experiment_id

    def test_list_by_session_limit(self, store):
        for i in range(5):
            store.write_experiment(_make_record(session_id="sess_lim", metric_value=float(i)))
        results = store.list_experiments("sess_lim", limit=3)
        assert len(results) == 3

    def test_list_by_metric(self, store):
        e1 = _make_record(metric_name="latency_ms", metric_value=100.0)
        e2 = _make_record(metric_name="latency_ms", metric_value=95.0)
        e3 = _make_record(metric_name="throughput", metric_value=50.0)
        store.write_experiment(e1)
        store.write_experiment(e2)
        store.write_experiment(e3)

        results = store.list_experiments_by_metric("latency_ms")
        assert len(results) == 2
        assert results[0].experiment_id == e1.experiment_id
        assert results[1].experiment_id == e2.experiment_id

    def test_list_by_metric_with_session(self, store):
        e1 = _make_record(session_id="sess_x", metric_name="latency_ms", metric_value=100.0)
        e2 = _make_record(session_id="sess_y", metric_name="latency_ms", metric_value=200.0)
        store.write_experiment(e1)
        store.write_experiment(e2)

        results = store.list_experiments_by_metric("latency_ms", session_id="sess_x")
        assert len(results) == 1
        assert results[0].experiment_id == e1.experiment_id

    def test_links_to_receipt(self, store):
        record = _make_record(receipt_id="rct_some_uuid")
        store.write_experiment(record)
        loaded = store.read_experiment(record.experiment_id)
        assert loaded.receipt_id == "rct_some_uuid"

    def test_links_to_packets(self, store):
        record = _make_record(packet_ids=["ctx_one", "ctx_two"])
        store.write_experiment(record)
        loaded = store.read_experiment(record.experiment_id)
        assert loaded.packet_ids == ["ctx_one", "ctx_two"]

    def test_baseline_reference(self, store):
        baseline = _make_record()
        store.write_experiment(baseline)
        experiment = _make_record(
            baseline_value=10.0,
            baseline_experiment_id=baseline.experiment_id,
        )
        store.write_experiment(experiment)
        loaded = store.read_experiment(experiment.experiment_id)
        assert loaded.baseline_value == 10.0
        assert loaded.baseline_experiment_id == baseline.experiment_id

    def test_metric_value_int_normalized_on_write(self, store):
        record = _make_record(metric_value=42)
        store.write_experiment(record)
        loaded = store.read_experiment(record.experiment_id)
        assert isinstance(loaded.metric_value, float)
        assert loaded.metric_value == 42.0

    def test_append_only_no_update(self, store):
        record = _make_record()
        store.write_experiment(record)
        # Verify no update/delete methods exist
        assert not hasattr(store, "update_experiment")
        assert not hasattr(store, "delete_experiment")

    def test_params_summary_stored_as_string(self, store):
        record = _make_record(params_summary="write_file: path='test.txt', 20 bytes")
        store.write_experiment(record)
        loaded = store.read_experiment(record.experiment_id)
        assert isinstance(loaded.params_summary, str)
        assert loaded.params_summary == "write_file: path='test.txt', 20 bytes"

    def test_params_summary_none(self, store):
        record = _make_record(params_summary=None)
        store.write_experiment(record)
        loaded = store.read_experiment(record.experiment_id)
        assert loaded.params_summary is None

    def test_tags_and_metadata_roundtrip(self, store):
        record = _make_record(
            tags=["write", "experiment"],
            metadata={"author": "test", "iteration": 3},
        )
        store.write_experiment(record)
        loaded = store.read_experiment(record.experiment_id)
        assert loaded.tags == ["write", "experiment"]
        assert loaded.metadata == {"author": "test", "iteration": 3}
