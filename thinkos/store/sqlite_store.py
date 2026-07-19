"""SQLite-backed append-only store for context packets and receipts."""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional
from thinkos.schema.context_packet import ContextPacket, check_dag_depth
from thinkos.schema.experiment_record import ExperimentRecord, normalize as normalize_experiment
from thinkos.schema.handoff_record import (
    HandoffRecord,
    parse_timestamp as parse_handoff_timestamp,
    validate as validate_handoff,
)
from thinkos.schema.receipt import Receipt, Action, Result, GateInfo


class CycleError(Exception):
    pass


class DepthError(Exception):
    pass


class DuplicateError(Exception):
    pass


class HandoffReferenceError(Exception):
    pass


_UNSET = object()  # sentinel to distinguish "not provided" from "explicitly None"


class SQLiteStore:
    """Append-only store using SQLite. No update or delete methods exposed."""

    def __init__(self, db_path: str = ":memory:"):
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=DELETE")
        self._init_tables()

    def _init_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS packets (
                packet_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                parent_id TEXT,
                timestamp TEXT NOT NULL,
                kind TEXT NOT NULL,
                source TEXT NOT NULL,
                content_text TEXT NOT NULL,
                content_structured TEXT,
                tags TEXT,
                refs TEXT,
                metadata TEXT
            );
            CREATE TABLE IF NOT EXISTS receipts (
                receipt_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                action_type TEXT NOT NULL,
                action_tool TEXT,
                action_params TEXT,
                action_agent TEXT NOT NULL,
                result_status TEXT NOT NULL,
                result_summary TEXT NOT NULL,
                result_packet_ids TEXT,
                result_error TEXT,
                gate_name TEXT,
                gate_decision TEXT,
                gate_reason TEXT,
                supersedes TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_receipts_session_seq
                ON receipts(session_id, sequence);
            CREATE INDEX IF NOT EXISTS idx_packets_session
                ON packets(session_id, kind);
            CREATE TABLE IF NOT EXISTS experiments (
                experiment_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                params_summary TEXT,
                metric_name TEXT NOT NULL,
                metric_value REAL NOT NULL,
                baseline_value REAL,
                baseline_experiment_id TEXT,
                decision TEXT NOT NULL,
                decision_reason TEXT,
                receipt_id TEXT,
                packet_ids TEXT,
                tags TEXT,
                metadata TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_experiments_session
                ON experiments(session_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_experiments_metric
                ON experiments(metric_name, session_id);
            CREATE TABLE IF NOT EXISTS handoffs (
                handoff_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                source_session_id TEXT NOT NULL,
                target_session_id TEXT NOT NULL,
                source_agent TEXT NOT NULL,
                target_agent TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                expires_at TEXT,
                purpose_summary TEXT NOT NULL,
                packet_ids TEXT NOT NULL,
                receipt_ids TEXT NOT NULL,
                omitted_packet_count INTEGER NOT NULL DEFAULT 0,
                omissions_summary TEXT,
                evidence_policy TEXT NOT NULL,
                authority_transfer TEXT NOT NULL,
                requires_fresh_approval INTEGER NOT NULL,
                tags TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_handoffs_target
                ON handoffs(target_session_id, timestamp, handoff_id);
            CREATE TABLE IF NOT EXISTS envelopes (
                envelope_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                handoff_id TEXT NOT NULL,
                source_principal TEXT NOT NULL,
                source_session_id TEXT NOT NULL,
                target_session_intent TEXT NOT NULL,
                store_namespace TEXT NOT NULL,
                provider TEXT NOT NULL,
                issuer TEXT NOT NULL,
                policy_version TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_envelopes_handoff
                ON envelopes(handoff_id);
            CREATE TABLE IF NOT EXISTS adapter_audits (
                audit_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                operation TEXT NOT NULL,
                handoff_id TEXT,
                principal TEXT NOT NULL,
                session_id TEXT NOT NULL,
                store_namespace TEXT NOT NULL,
                provider TEXT NOT NULL,
                issuer TEXT NOT NULL,
                policy_version TEXT NOT NULL,
                result_status TEXT NOT NULL,
                result_reason TEXT,
                timestamp TEXT NOT NULL
            );
        """)
        self._conn.commit()

    def _get_parent_depth(self, packet_id: str) -> int:
        """Traverse parent_id chain to find depth. Returns 0 if not found."""
        depth = 0
        current = packet_id
        visited = set()
        while current is not None:
            if current in visited:
                return depth  # cycle detected, return current depth
            visited.add(current)
            row = self._conn.execute(
                "SELECT parent_id FROM packets WHERE packet_id = ?", (current,)
            ).fetchone()
            if row is None:
                break
            current = row[0]
            depth += 1
            if depth > 5:
                break
        return depth

    def _check_cycle(self, packet: ContextPacket) -> bool:
        """Return True if writing this packet would create a cycle."""
        if packet.parent_id is None:
            return False
        current = packet.parent_id
        visited = set()
        while current is not None:
            if current == packet.packet_id:
                return True
            if current in visited:
                return True
            visited.add(current)
            row = self._conn.execute(
                "SELECT parent_id FROM packets WHERE packet_id = ?", (current,)
            ).fetchone()
            if row is None:
                break
            current = row[0]
        return False

    def write_packet(self, packet: ContextPacket):
        if self._conn.execute(
            "SELECT 1 FROM packets WHERE packet_id = ?", (packet.packet_id,)
        ).fetchone():
            raise DuplicateError(f"packet_id '{packet.packet_id}' already exists")

        if self._check_cycle(packet):
            raise CycleError("Writing this packet would create a cycle")

        depth = self._get_parent_depth(packet.parent_id) if packet.parent_id else 0
        if depth >= 5:
            raise DepthError(f"DAG depth exceeds maximum of 5")

        content_structured = json.dumps(packet.content.get("structured")) if packet.content.get("structured") else None
        tags = json.dumps(packet.tags) if packet.tags else None
        refs = json.dumps(packet.refs) if packet.refs else None
        metadata = json.dumps(packet.metadata) if packet.metadata else None

        self._conn.execute(
            """INSERT INTO packets
               (packet_id, schema_version, session_id, parent_id, timestamp, kind, source,
                content_text, content_structured, tags, refs, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (packet.packet_id, packet.schema_version, packet.session_id, packet.parent_id,
             packet.timestamp, packet.kind, packet.source,
             packet.content.get("text", ""), content_structured, tags, refs, metadata)
        )
        self._conn.commit()

    def read_packet(self, packet_id: str) -> ContextPacket | None:
        row = self._conn.execute(
            "SELECT * FROM packets WHERE packet_id = ?", (packet_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_packet(row)

    def list_packets(self, session_id: str | None = None, kind: str | None = None,
                     tags: list[str] | None = None,
                     parent_id: str | None | object = _UNSET,
                     source: str | None = None,
                     time_range: tuple[str, str] | None = None,
                     order: str = "asc", limit: int = 100) -> list[ContextPacket]:
        query = "SELECT * FROM packets WHERE 1=1"
        params = []
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if kind:
            query += " AND kind = ?"
            params.append(kind)
        if tags:
            for tag in tags:
                query += " AND tags LIKE ?"
                params.append(f'%"{tag}"%')
        if parent_id is not _UNSET:
            if parent_id is not None:
                query += " AND parent_id = ?"
                params.append(parent_id)
            else:
                query += " AND parent_id IS NULL"
        if source:
            query += " AND source = ?"
            params.append(source)
        if time_range:
            start, end = time_range
            query += " AND timestamp >= ? AND timestamp <= ?"
            params.extend([start, end])
        if order == "asc":
            query += " ORDER BY timestamp ASC, packet_id ASC"
        elif order == "desc":
            query += " ORDER BY timestamp DESC, packet_id DESC"
        else:
            raise ValueError(f"order must be 'asc' or 'desc', got '{order}'")
        query += " LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_packet(r) for r in rows]

    def _row_to_packet(self, row) -> ContextPacket:
        return ContextPacket(
            packet_id=row[0],
            schema_version=row[1],
            session_id=row[2],
            parent_id=row[3],
            timestamp=row[4],
            kind=row[5],
            source=row[6],
            content={"text": row[7], "structured": json.loads(row[8]) if row[8] else None},
            tags=json.loads(row[9]) if row[9] else [],
            refs=json.loads(row[10]) if row[10] else [],
            metadata=json.loads(row[11]) if row[11] else {},
        )

    def write_receipt(self, receipt: Receipt):
        if self._conn.execute(
            "SELECT 1 FROM receipts WHERE receipt_id = ?", (receipt.receipt_id,)
        ).fetchone():
            raise DuplicateError(f"receipt_id '{receipt.receipt_id}' already exists")

        action_params = json.dumps(receipt.action.params) if receipt.action.params else None
        packet_ids = json.dumps(receipt.result.packet_ids) if receipt.result.packet_ids else None

        self._conn.execute(
            """INSERT INTO receipts
               (receipt_id, schema_version, session_id, sequence, timestamp,
                action_type, action_tool, action_params, action_agent,
                result_status, result_summary, result_packet_ids, result_error,
                gate_name, gate_decision, gate_reason, supersedes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (receipt.receipt_id, receipt.schema_version, receipt.session_id, receipt.sequence,
             receipt.timestamp,
             receipt.action.type, receipt.action.tool, action_params, receipt.action.agent,
             receipt.result.status, receipt.result.summary, packet_ids, receipt.result.error,
             receipt.gate.gate_name if receipt.gate else None,
             receipt.gate.decision if receipt.gate else None,
             receipt.gate.reason if receipt.gate else None,
             receipt.supersedes)
        )
        self._conn.commit()

    def write_receipt_and_packet(self, receipt: Receipt, packet: ContextPacket | None):
        """Atomically persist a receipt and its linked context packet.

        Both are inserted inside a single SQLite transaction.  If *any*
        step fails the entire transaction is rolled back — no partial pair
        is ever committed.

        Only DepthError triggers a parent-free retry (after a full rollback
        of the original attempt).  DuplicateError and CycleError propagate
        to the caller with no partial pair left in the database.

        When *packet* is None only the receipt is written (no transaction
        wrapping needed — the caller is writing a standalone receipt).
        """
        if packet is None:
            self.write_receipt(receipt)
            return

        # --- receipt validation (must pass before we touch the DB) ---
        if self._conn.execute(
            "SELECT 1 FROM receipts WHERE receipt_id = ?", (receipt.receipt_id,)
        ).fetchone():
            raise DuplicateError(f"receipt_id '{receipt.receipt_id}' already exists")

        # --- packet validation (must pass before we touch the DB) ---
        if self._conn.execute(
            "SELECT 1 FROM packets WHERE packet_id = ?", (packet.packet_id,)
        ).fetchone():
            raise DuplicateError(f"packet_id '{packet.packet_id}' already exists")

        if self._check_cycle(packet):
            raise CycleError("Writing this packet would create a cycle")

        depth = self._get_parent_depth(packet.parent_id) if packet.parent_id else 0
        if depth >= 5:
            raise DepthError(f"DAG depth exceeds maximum of 5")

        # --- serialise once ---
        action_params = json.dumps(receipt.action.params) if receipt.action.params else None
        packet_ids = json.dumps(receipt.result.packet_ids) if receipt.result.packet_ids else None
        content_structured = json.dumps(packet.content.get("structured")) if packet.content.get("structured") else None
        p_tags = json.dumps(packet.tags) if packet.tags else None
        p_refs = json.dumps(packet.refs) if packet.refs else None
        p_metadata = json.dumps(packet.metadata) if packet.metadata else None

        # --- transaction: insert receipt then packet, roll back on any failure ---
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute(
                """INSERT INTO receipts
                   (receipt_id, schema_version, session_id, sequence, timestamp,
                    action_type, action_tool, action_params, action_agent,
                    result_status, result_summary, result_packet_ids, result_error,
                    gate_name, gate_decision, gate_reason, supersedes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (receipt.receipt_id, receipt.schema_version, receipt.session_id,
                 receipt.sequence, receipt.timestamp,
                 receipt.action.type, receipt.action.tool, action_params,
                 receipt.action.agent,
                 receipt.result.status, receipt.result.summary, packet_ids,
                 receipt.result.error,
                 receipt.gate.gate_name if receipt.gate else None,
                 receipt.gate.decision if receipt.gate else None,
                 receipt.gate.reason if receipt.gate else None,
                 receipt.supersedes)
            )
            self._conn.execute(
                """INSERT INTO packets
                   (packet_id, schema_version, session_id, parent_id, timestamp,
                    kind, source, content_text, content_structured, tags, refs, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (packet.packet_id, packet.schema_version, packet.session_id,
                 packet.parent_id, packet.timestamp,
                 packet.kind, packet.source,
                 packet.content.get("text", ""), content_structured,
                 p_tags, p_refs, p_metadata)
            )
            self._conn.commit()
        except (DuplicateError, CycleError, DepthError):
            self._conn.rollback()
            raise
        except Exception:
            self._conn.rollback()
            raise

    def read_receipt(self, receipt_id: str) -> Receipt | None:
        row = self._conn.execute(
            "SELECT * FROM receipts WHERE receipt_id = ?", (receipt_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_receipt(row)

    def list_receipts(self, session_id: str | None = None, limit: int = 100) -> list[Receipt]:
        query = "SELECT * FROM receipts WHERE 1=1"
        params = []
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        query += " ORDER BY sequence ASC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_receipt(r) for r in rows]

    def rehydrate(self, session_id: str, receipt_limit: int = 10000) -> tuple[list[ContextPacket], list[Receipt]]:
        receipts = self.list_receipts(session_id=session_id, limit=receipt_limit)
        packet_ids = set()
        for r in receipts:
            if r.result.packet_ids:
                packet_ids.update(r.result.packet_ids)
        packets = []
        for pid in packet_ids:
            p = self.read_packet(pid)
            if p:
                packets.append(p)
        # Deterministic ordering: timestamp DESC, packet_id DESC
        packets.sort(key=lambda p: (p.timestamp, p.packet_id), reverse=True)
        return packets, receipts

    def _row_to_receipt(self, row) -> Receipt:
        action_params = json.loads(row[7]) if row[7] else None
        packet_ids = json.loads(row[11]) if row[11] else []
        gate = None
        if row[13] or row[14]:
            gate = GateInfo(gate_name=row[13], decision=row[14], reason=row[15])
        return Receipt(
            receipt_id=row[0],
            schema_version=row[1],
            session_id=row[2],
            sequence=row[3],
            timestamp=row[4],
            action=Action(type=row[5], tool=row[6], params=action_params, agent=row[8]),
            result=Result(status=row[9], summary=row[10], packet_ids=packet_ids, error=row[12]),
            gate=gate,
            supersedes=row[16],
        )

    def write_experiment(self, record: ExperimentRecord):
        if self._conn.execute(
            "SELECT 1 FROM experiments WHERE experiment_id = ?", (record.experiment_id,)
        ).fetchone():
            raise DuplicateError(f"experiment_id '{record.experiment_id}' already exists")

        normalize_experiment(record)

        params_summary = record.params_summary
        baseline_value = record.baseline_value
        packet_ids = json.dumps(record.packet_ids) if record.packet_ids else None
        tags = json.dumps(record.tags) if record.tags else None
        metadata = json.dumps(record.metadata) if record.metadata else None

        self._conn.execute(
            """INSERT INTO experiments
               (experiment_id, schema_version, session_id, timestamp, tool_name,
                params_summary, metric_name, metric_value, baseline_value,
                baseline_experiment_id, decision, decision_reason, receipt_id,
                packet_ids, tags, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (record.experiment_id, record.schema_version, record.session_id,
             record.timestamp, record.tool_name,
             params_summary, record.metric_name, record.metric_value,
             baseline_value, record.baseline_experiment_id,
             record.decision, record.decision_reason, record.receipt_id,
             packet_ids, tags, metadata)
        )
        self._conn.commit()

    def read_experiment(self, experiment_id: str) -> ExperimentRecord | None:
        row = self._conn.execute(
            "SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_experiment(row)

    def list_experiments(self, session_id: str, limit: int = 100) -> list[ExperimentRecord]:
        rows = self._conn.execute(
            "SELECT * FROM experiments WHERE session_id = ? ORDER BY timestamp ASC LIMIT ?",
            (session_id, limit)
        ).fetchall()
        return [self._row_to_experiment(r) for r in rows]

    def list_experiments_by_metric(self, metric_name: str, session_id: str | None = None,
                                   limit: int = 100) -> list[ExperimentRecord]:
        if session_id:
            rows = self._conn.execute(
                "SELECT * FROM experiments WHERE metric_name = ? AND session_id = ? ORDER BY timestamp ASC LIMIT ?",
                (metric_name, session_id, limit)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM experiments WHERE metric_name = ? ORDER BY timestamp ASC LIMIT ?",
                (metric_name, limit)
            ).fetchall()
        return [self._row_to_experiment(r) for r in rows]

    def _row_to_experiment(self, row) -> ExperimentRecord:
        return ExperimentRecord(
            experiment_id=row[0],
            schema_version=row[1],
            session_id=row[2],
            timestamp=row[3],
            tool_name=row[4],
            params_summary=row[5],
            metric_name=row[6],
            metric_value=row[7],
            baseline_value=row[8],
            baseline_experiment_id=row[9],
            decision=row[10],
            decision_reason=row[11],
            receipt_id=row[12],
            packet_ids=json.loads(row[13]) if row[13] else [],
            tags=json.loads(row[14]) if row[14] else [],
            metadata=json.loads(row[15]) if row[15] else {},
        )

    def get_latest_packet_id(self, session_id: str) -> str | None:
        """Return the packet_id of the most recent packet for a session.

        Uses timestamp DESC, rowid DESC for deterministic ordering when
        timestamps are identical. Returns None if the session has no packets.
        """
        row = self._conn.execute(
            "SELECT packet_id FROM packets WHERE session_id = ? "
            "ORDER BY timestamp DESC, rowid DESC LIMIT 1",
            (session_id,)
        ).fetchone()
        return row[0] if row else None

    def get_packet_chain(self, packet_id: str, max_packets: int = 5) -> list[ContextPacket]:
        """Walk parent_id links from *packet_id* toward root.

        Returns an ordered list ``[root, ..., packet]`` (root first, the
        requested packet last), bounded by *max_packets*.

        Safety guarantees:
        - Returns ``[]`` if *packet_id* is missing.
        - Stops cleanly if a parent packet is missing.
        - Stops cleanly if a parent packet belongs to a different session.
        - Stops cleanly if a cycle is detected (visited set).
        - Never returns more than *max_packets* packets.
        - Read-only — does not mutate store state.
        """
        if max_packets <= 0:
            return []

        start = self.read_packet(packet_id)
        if start is None:
            return []

        session_id = start.session_id
        chain: list[ContextPacket] = [start]
        visited: set[str] = {start.packet_id}
        current = start.parent_id

        while current is not None and len(chain) < max_packets:
            if current in visited:
                break  # cycle detected
            visited.add(current)

            parent = self.read_packet(current)
            if parent is None:
                break  # missing parent
            if parent.session_id != session_id:
                break  # cross-session boundary

            chain.insert(0, parent)
            current = parent.parent_id

        return chain

    def get_packet_children(self, packet_id: str, limit: int = 100) -> list[ContextPacket]:
        """Return direct children of *packet_id*.

        Children are packets whose ``parent_id`` equals *packet_id* and whose
        ``session_id`` matches the parent's session.

        Returns packets ordered by ``timestamp DESC, packet_id DESC``.
        Returns ``[]`` if *packet_id* is missing or has no children.
        Read-only — does not mutate store state.
        """
        parent = self.read_packet(packet_id)
        if parent is None:
            return []
        rows = self._conn.execute(
            "SELECT * FROM packets WHERE parent_id = ? AND session_id = ? "
            "ORDER BY timestamp DESC, packet_id DESC LIMIT ?",
            (packet_id, parent.session_id, limit)
        ).fetchall()
        return [self._row_to_packet(r) for r in rows]

    def write_handoff(self, record: HandoffRecord, ctx):
        """DEPRECATED — use write_handoff_with_envelope for all handoff creation.

        This method is retained only for internal test compatibility.
        It requires a valid VerifiedExecutionContext but does NOT create
        a security envelope. Callers must use write_handoff_with_envelope.
        """
        from thinkos.schema.verified_context import VerifiedExecutionContext
        if not isinstance(ctx, VerifiedExecutionContext):
            raise TypeError("ctx must be a VerifiedExecutionContext")
        if not ctx.is_verified:
            raise PermissionError("unverified context")
        if ctx.is_expired():
            raise PermissionError("expired context")
        raise PermissionError(
            "write_handoff is deprecated; use write_handoff_with_envelope "
            "which atomically creates a security envelope"
        )

    def _verify_handoff_references(self, record: HandoffRecord):
        for packet_id in record.packet_ids:
            row = self._conn.execute(
                "SELECT session_id FROM packets WHERE packet_id = ?", (packet_id,)
            ).fetchone()
            if row is None:
                raise HandoffReferenceError(
                    f"referenced packet_id '{packet_id}' does not exist"
                )
            if row[0] != record.source_session_id:
                raise HandoffReferenceError(
                    f"referenced packet_id '{packet_id}' does not belong to source session"
                )

        for receipt_id in record.receipt_ids:
            row = self._conn.execute(
                "SELECT session_id FROM receipts WHERE receipt_id = ?", (receipt_id,)
            ).fetchone()
            if row is None:
                raise HandoffReferenceError(
                    f"referenced receipt_id '{receipt_id}' does not exist"
                )
            if row[0] != record.source_session_id:
                raise HandoffReferenceError(
                    f"referenced receipt_id '{receipt_id}' does not belong to source session"
                )

    def _row_to_handoff(self, row) -> HandoffRecord:
        return HandoffRecord(
            handoff_id=row[0],
            schema_version=row[1],
            source_session_id=row[2],
            target_session_id=row[3],
            source_agent=row[4],
            target_agent=row[5],
            timestamp=row[6],
            expires_at=row[7],
            purpose_summary=row[8],
            packet_ids=json.loads(row[9]),
            receipt_ids=json.loads(row[10]),
            omitted_packet_count=row[11],
            omissions_summary=row[12],
            evidence_policy=row[13],
            authority_transfer=row[14],
            requires_fresh_approval=bool(row[15]),
            tags=json.loads(row[16]),
        )

    def close(self):
        self._conn.close()

    # ------------------------------------------------------------------
    # TAA v0: Security envelope and audit support
    # ------------------------------------------------------------------

    def write_handoff_with_envelope(self, record: HandoffRecord, envelope, ctx):
        """Atomically write a HandoffRecord and its security envelope.

        ctx is required. Enforces before writing:
        - ctx is a VerifiedExecutionContext, verified, and unexpired
        - ctx.store_namespace == envelope.store_namespace
        - ctx.principal == envelope.source_principal
        - ctx.session_id == envelope.source_session_id
        - record.handoff_id == envelope.handoff_id
        - record.target_session_id == envelope.target_session_intent

        If any check fails, no partial write remains.
        """
        from thinkos.schema.verified_context import VerifiedExecutionContext
        if not isinstance(ctx, VerifiedExecutionContext):
            raise TypeError("ctx must be a VerifiedExecutionContext")
        if not ctx.is_verified:
            raise PermissionError("unverified context")
        if ctx.is_expired():
            raise PermissionError("expired context")
        if ctx.store_namespace != envelope.store_namespace:
            raise PermissionError("namespace mismatch between context and envelope")
        if ctx.principal != envelope.source_principal:
            raise PermissionError("principal mismatch between context and envelope")
        if ctx.session_id != envelope.source_session_id:
            raise PermissionError("session mismatch between context and envelope")
        if record.handoff_id != envelope.handoff_id:
            raise ValueError("handoff_id mismatch between record and envelope")
        if record.target_session_id != envelope.target_session_intent:
            raise ValueError("target_session mismatch between record and envelope")

        errors = validate_handoff(record)
        if errors:
            raise ValueError("invalid handoff record: " + "; ".join(errors))

        if self._conn.execute(
            "SELECT 1 FROM handoffs WHERE handoff_id = ?", (record.handoff_id,)
        ).fetchone():
            raise DuplicateError(f"handoff_id '{record.handoff_id}' already exists")

        # Override record source fields with verified envelope values BEFORE
        # reference verification, so evidence membership uses the envelope's
        # source session, not an independently supplied HandoffRecord field.
        record = HandoffRecord(
            handoff_id=record.handoff_id,
            schema_version=record.schema_version,
            source_session_id=envelope.source_session_id,
            target_session_id=record.target_session_id,
            source_agent=envelope.source_principal,
            target_agent=record.target_agent,
            timestamp=record.timestamp,
            expires_at=record.expires_at,
            purpose_summary=record.purpose_summary,
            packet_ids=record.packet_ids,
            receipt_ids=record.receipt_ids,
            omitted_packet_count=record.omitted_packet_count,
            omissions_summary=record.omissions_summary,
            evidence_policy=record.evidence_policy,
            authority_transfer=record.authority_transfer,
            requires_fresh_approval=record.requires_fresh_approval,
            tags=record.tags,
        )

        self._verify_handoff_references(record)

        try:
            self._conn.execute("BEGIN IMMEDIATE")
            # Write handoff record
            self._conn.execute(
                """INSERT INTO handoffs
                   (handoff_id, schema_version, source_session_id, target_session_id,
                    source_agent, target_agent, timestamp, expires_at, purpose_summary,
                    packet_ids, receipt_ids, omitted_packet_count, omissions_summary,
                    evidence_policy, authority_transfer, requires_fresh_approval, tags)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.handoff_id,
                    record.schema_version,
                    record.source_session_id,
                    record.target_session_id,
                    record.source_agent,
                    record.target_agent,
                    record.timestamp,
                    record.expires_at,
                    record.purpose_summary,
                    json.dumps(record.packet_ids),
                    json.dumps(record.receipt_ids),
                    record.omitted_packet_count,
                    record.omissions_summary,
                    record.evidence_policy,
                    record.authority_transfer,
                    int(record.requires_fresh_approval),
                    json.dumps(record.tags),
                ),
            )
            # Write security envelope
            self._conn.execute(
                """INSERT INTO envelopes
                   (envelope_id, schema_version, handoff_id,
                    source_principal, source_session_id, target_session_intent,
                    store_namespace, provider, issuer, policy_version, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    envelope.envelope_id,
                    envelope.schema_version,
                    envelope.handoff_id,
                    envelope.source_principal,
                    envelope.source_session_id,
                    envelope.target_session_intent,
                    envelope.store_namespace,
                    envelope.provider,
                    envelope.issuer,
                    envelope.policy_version,
                    envelope.created_at,
                ),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def read_envelope(self, handoff_id: str):
        """Read the security envelope for a handoff record.

        Returns None if no envelope exists (UNVERIFIED_LEGACY).
        """
        from thinkos.schema.security_envelope import HandoffSecurityEnvelope
        row = self._conn.execute(
            "SELECT * FROM envelopes WHERE handoff_id = ?", (handoff_id,)
        ).fetchone()
        if row is None:
            return None
        return HandoffSecurityEnvelope(
            envelope_id=row[0],
            schema_version=row[1],
            handoff_id=row[2],
            source_principal=row[3],
            source_session_id=row[4],
            target_session_intent=row[5],
            store_namespace=row[6],
            provider=row[7],
            issuer=row[8],
            policy_version=row[9],
            created_at=row[10],
        )

    def write_adapter_audit(self, audit):
        """Persist an adapter audit record. Best-effort."""
        self._conn.execute(
            """INSERT INTO adapter_audits
               (audit_id, schema_version, operation, handoff_id,
                principal, session_id, store_namespace,
                provider, issuer, policy_version,
                result_status, result_reason, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                audit.audit_id,
                audit.schema_version if hasattr(audit, 'schema_version') else 1,
                audit.operation,
                audit.handoff_id,
                audit.principal,
                audit.session_id,
                audit.store_namespace,
                audit.provider,
                audit.issuer,
                audit.policy_version,
                audit.result_status,
                audit.result_reason,
                audit.timestamp,
            ),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # TAA v0: Context-enforcing handoff methods (defense in depth)
    # ------------------------------------------------------------------

    def read_handoff(self, handoff_id: str, ctx) -> HandoffRecord | None:
        """Read a handoff record.

        ctx is required. Enforces context validity, envelope presence,
        and namespace match. Legacy records (no envelope) are denied.
        """
        from thinkos.schema.verified_context import VerifiedExecutionContext
        if not isinstance(ctx, VerifiedExecutionContext):
            raise TypeError("ctx must be a VerifiedExecutionContext")
        if not ctx.is_verified:
            raise PermissionError("unverified context")
        if ctx.is_expired():
            raise PermissionError("expired context")
        # Envelope must exist (legacy denial)
        envelope = self.read_envelope(handoff_id)
        if envelope is None:
            raise HandoffReferenceError("handoff has no security envelope (UNVERIFIED_LEGACY)")
        if ctx.store_namespace != envelope.store_namespace:
            raise PermissionError("namespace mismatch")
        # Session binding: source or target session may read
        if ctx.session_id != envelope.source_session_id and ctx.session_id != envelope.target_session_intent:
            raise PermissionError("session not authorized for this handoff")

        row = self._conn.execute(
            "SELECT * FROM handoffs WHERE handoff_id = ?", (handoff_id,)
        ).fetchone()
        return self._row_to_handoff(row) if row else None

    def list_handoffs_for_target(
        self,
        target_session_id: str,
        ctx,
        target_agent: str | None = None,
        limit: int = 100,
    ) -> list[HandoffRecord]:
        """List handoffs for a target session.

        ctx is required. Filters to only envelope-backed records
        and enforces namespace match.
        """
        if not isinstance(target_session_id, str) or not target_session_id:
            raise ValueError("target_session_id is required")
        if target_agent is not None and (
            not isinstance(target_agent, str) or not target_agent
        ):
            raise ValueError("target_agent must be a non-empty string or None")
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("limit must be an integer")
        if limit <= 0:
            return []

        from thinkos.schema.verified_context import VerifiedExecutionContext
        if not isinstance(ctx, VerifiedExecutionContext):
            raise TypeError("ctx must be a VerifiedExecutionContext")
        if not ctx.is_verified:
            raise PermissionError("unverified context")
        if ctx.is_expired():
            raise PermissionError("expired context")

        # Session binding: caller may only list for their own session
        if ctx.session_id != target_session_id:
            raise PermissionError("session mismatch: may only list for own session")

        query = "SELECT h.* FROM handoffs h"
        params: list = []

        # Only return records that have a valid envelope with matching namespace
        query += " INNER JOIN envelopes e ON e.handoff_id = h.handoff_id"
        query += " WHERE e.store_namespace = ?"
        params.append(ctx.store_namespace)
        query += " AND h.target_session_id = ?"
        params.append(target_session_id)

        if target_agent is not None:
            query += " AND h.target_agent = ?"
            params.append(target_agent)

        query += " ORDER BY h.timestamp DESC, h.handoff_id DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_handoff(row) for row in rows]

    def resolve_handoff(self, handoff_id: str, ctx) -> dict:
        """Resolve exactly the referenced evidence without expanding either DAG.

        ctx is required. Enforces context validity, envelope presence,
        and namespace match.
        """
        from thinkos.schema.verified_context import VerifiedExecutionContext
        if not isinstance(ctx, VerifiedExecutionContext):
            raise TypeError("ctx must be a VerifiedExecutionContext")
        if not ctx.is_verified:
            raise PermissionError("unverified context")
        if ctx.is_expired():
            raise PermissionError("expired context")
        # Envelope must exist
        envelope = self.read_envelope(handoff_id)
        if envelope is None:
            raise HandoffReferenceError("handoff has no security envelope (UNVERIFIED_LEGACY)")
        if ctx.store_namespace != envelope.store_namespace:
            raise PermissionError("namespace mismatch")
        # Session binding: only the target session may resolve
        if ctx.session_id != envelope.target_session_intent:
            raise PermissionError("session not authorized: only target session may resolve")

        record = self._read_handoff_raw(handoff_id)
        if record is None:
            raise HandoffReferenceError(f"handoff_id '{handoff_id}' does not exist")

        errors = validate_handoff(record)
        if errors:
            raise HandoffReferenceError(
                "stored handoff record is invalid: " + "; ".join(errors)
            )
        self._verify_handoff_references(record)

        packets = [self.read_packet(packet_id) for packet_id in record.packet_ids]
        receipts = [self.read_receipt(receipt_id) for receipt_id in record.receipt_ids]

        expired = False
        if record.expires_at is not None:
            expires_at = parse_handoff_timestamp(record.expires_at)
            if expires_at is None:
                raise HandoffReferenceError("stored handoff expires_at is invalid")
            expired = expires_at <= datetime.now(timezone.utc)

        return {
            "record": record,
            "packets": packets,
            "receipts": receipts,
            "expired": expired,
        }

    def _read_handoff_raw(self, handoff_id: str) -> HandoffRecord | None:
        """Read a handoff record without security checks (internal use only)."""
        row = self._conn.execute(
            "SELECT * FROM handoffs WHERE handoff_id = ?", (handoff_id,)
        ).fetchone()
        return self._row_to_handoff(row) if row else None
