"""Thin State Reconciliation v0 (TSR v0) — read-only recorded-vs-live status.

Contract:
TSR v1.3 base contract, prospectively amended by
docs/specs/TSR_V0_SPEC_v1.4.md for worktree observation and reconciliation.

v1.1 (SHA-256 eb15926a63027a295029cb9bde9f8235c40728862cf3fd8c1493f85f29afdf6a)
remains the prior frozen record.

Read-only. No writes, no network, no shell, no authority changes.
All git is invoked via subprocess argument lists only.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from thinkos.onboarding import doctor as _doctor

SCHEMA_VERSION = "tsr.v0"

_PROBE_KEYS = (
    "repository_presence",
    "head_sha",
    "branch",
    "upstream",
    "worktree_dirty",
)

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

_STATE_FILE_NAME = "project-state.json"


def _resolve_project_dir(project_path: str | None) -> Path:
    """Resolve the project directory to an absolute path."""
    if project_path is None:
        return Path.cwd().resolve()
    return Path(project_path).expanduser().resolve()


def _state_file_path(project_dir: Path) -> Path:
    return project_dir / ".thinkos" / _STATE_FILE_NAME


def _load_state_file(state_file: Path) -> dict | None:
    """Return the state-file dict, or None when missing or unparseable."""
    try:
        raw = state_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, MemoryError):
        # Missing, unreadable, non-UTF-8, or resource-exhausted read →
        # fail-closed (spec §2). MemoryError must not escape into a traceback:
        # an unparseable-under-resources file still yields UNKNOWN.
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError, RecursionError, MemoryError):
        # Malformed, wrong-typed, pathologically nested, or resource-exhausted
        # JSON → fail-closed (spec §2). RecursionError is a RuntimeError
        # subclass and escapes the ValueError handler, so it must be caught
        # explicitly; MemoryError likewise escapes and must fail closed.
        return None
    if not isinstance(data, dict):
        return None
    return data


def _validate_recorded_probe(key: str, probe: object) -> bool:
    """Validate a recorded probe against the frozen schema (exact structure).

    Extra/missing keys inside the probe object, wrong value types, and
    non-40-lowercase-hex sha values are all malformed (probe excluded).
    """
    if not isinstance(probe, dict):
        return False
    if key == "repository_presence":
        return set(probe) == {"exists"} and isinstance(probe.get("exists"), bool)
    if key == "head_sha":
        value = probe.get("value")
        return (
            set(probe) == {"value"}
            and isinstance(value, str)
            and bool(_SHA_RE.fullmatch(value))
        )
    if key == "branch":
        if set(probe) != {"detached", "branch"}:
            return False
        detached = probe.get("detached")
        branch = probe.get("branch")
        if not isinstance(detached, bool):
            return False
        if detached:
            return branch is None
        return isinstance(branch, str) and branch != ""
    if key == "upstream":
        configured = probe.get("configured")
        if not isinstance(configured, bool):
            return False
        if configured:
            ref = probe.get("ref")
            sha = probe.get("sha")
            return (
                set(probe) == {"configured", "ref", "sha"}
                and isinstance(ref, str)
                and ref != ""
                and isinstance(sha, str)
                and bool(_SHA_RE.fullmatch(sha))
            )
        return set(probe) == {"configured"}
    if key == "worktree_dirty":
        return set(probe) == {"dirty"} and isinstance(probe.get("dirty"), bool)
    return False


_DECODE_FAILED = object()


def _git_run(project_dir: Path, args: list[str]) -> subprocess.CompletedProcess | None:
    """Run a read-only git command; None on subprocess launch failure.

    Git output is captured as raw bytes and decoded as UTF-8 strictly.
    If decoding cannot be performed faithfully, the affected probe must be
    treated as unevaluable (spec §6 fail-closed): the marker below propagates
    to the probe so it is excluded rather than compared on corrupted bytes.
    Invalid bytes are NEVER silently normalized with replacement characters,
    because that could convert UNKNOWN into a false CURRENT/STALE judgment.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            cwd=str(project_dir),
        )
    except (OSError, MemoryError):
        # Launch failure or resource-exhausted output buffering → probe
        # unevaluable (spec §6 fail-closed). MemoryError must not escape.
        return None

    def decode(data: bytes) -> str | object:
        try:
            return data.decode("utf-8")
        except (UnicodeDecodeError, MemoryError):
            return _DECODE_FAILED

    stdout = decode(result.stdout)
    stderr = decode(result.stderr)
    if stdout is _DECODE_FAILED or stderr is _DECODE_FAILED:
        return _DecodeFailedProcess()
    return subprocess.CompletedProcess(
        result.args, result.returncode, stdout=stdout, stderr=stderr
    )


class _DecodeFailedProcess:
    """Stand-in for a git result whose output could not be decoded faithfully.

    The probe layer treats this as unevaluable (live value unavailable),
    which fail-closes to UNKNOWN when it is the only probe or excludes the
    probe otherwise — never a false match on corrupted bytes.
    """

    returncode = 1
    stdout = ""
    stderr = "decode-failed"


def _probe_repository_presence(project_dir: Path) -> dict | None:
    """git rev-parse --git-dir: exit 0 -> {exists: true}, else {exists: false}."""
    result = _git_run(project_dir, ["rev-parse", "--git-dir"])
    if result is None or isinstance(result, _DecodeFailedProcess):
        return None
    return {"exists": True} if result.returncode == 0 else {"exists": False}


def _probe_head_sha(project_dir: Path) -> dict | None:
    """git rev-parse HEAD: exit 0 -> {value: <40-hex>}; failure -> non-evaluated."""
    result = _git_run(project_dir, ["rev-parse", "HEAD"])
    if result is None or result.returncode != 0:
        return None
    value = result.stdout.strip()
    if not _SHA_RE.fullmatch(value):
        return None
    return {"value": value}


def _probe_branch(project_dir: Path) -> dict | None:
    """symbolic-ref -> attached; else rev-parse HEAD ok -> detached; else non-evaluated."""
    result = _git_run(project_dir, ["symbolic-ref", "-q", "--short", "HEAD"])
    if result is None or isinstance(result, _DecodeFailedProcess):
        # symbolic-ref could not be launched (None) or its output could not be
        # decoded faithfully: treat the probe as unevaluable (spec §6
        # fail-closed). Do NOT fall through to rev-parse HEAD and misreport
        # the repository as detached on a launch failure.
        return None
    if result.returncode == 0:
        name = result.stdout.strip()
        if name:
            return {"detached": False, "branch": name}
        return None
    head = _git_run(project_dir, ["rev-parse", "HEAD"])
    if head is not None and head.returncode == 0:
        return {"detached": True, "branch": None}
    return None


def _probe_upstream(project_dir: Path) -> dict | None:
    """Both upstream refs resolve -> {configured: true, ...}; else {configured: false}."""
    ref_result = _git_run(project_dir, ["rev-parse", "--abbrev-ref", "@{upstream}"])
    if ref_result is None or isinstance(ref_result, _DecodeFailedProcess):
        return None
    if ref_result.returncode != 0:
        return {"configured": False}
    sha_result = _git_run(project_dir, ["rev-parse", "@{upstream}"])
    if sha_result is None or isinstance(sha_result, _DecodeFailedProcess):
        return None
    if sha_result.returncode != 0:
        return {"configured": False}
    ref = ref_result.stdout.strip()
    sha = sha_result.stdout.strip()
    if not ref or not _SHA_RE.fullmatch(sha):
        return None
    return {"configured": True, "ref": ref, "sha": sha}


_HAZARD_UNSAFE = "UNSAFE"

_MAX_PATHS_PER_BATCH = 128
_MAX_BYTES_PER_BATCH = 8192

_MODE_RE = re.compile(r"^[0-7]{6}$")
_STAGE_RE = re.compile(r"^[0-3]$")


def _worktree_hazard_gate(project_dir: Path) -> str | None:
    """TSR v1.4 read-only hazard gate.

    Returns "SAFE" only when no execution-capable conversion/filter
    configuration is positively established; "UNSAFE" when an effective
    driver can execute through clean/process behavior; None (UNEVALUABLE)
    on any launch failure, decode failure, parse ambiguity, incomplete
    enumeration, or otherwise uncertain observation.

    Order: (1) gitlink detection, (2) tracked-path enumeration,
    (3) bounded attribute batching, (4) driver configuration inspection.
    Executes no filter/helper, writes nothing, accesses no network, and
    mutates no Git state. _git_run is unchanged.
    """
    # 1) GITLINK DETECTION FIRST — any tracked mode-160000 gitlink is a
    #    conservative hazard: worktree_dirty is UNEVALUABLE and git status
    #    MUST NOT be invoked (spec v1.4 §3b).
    #    NOTE: every gate command carries the command-local fsmonitor
    #    suppression (-c core.fsmonitor=) because ls-files/check-attr also
    #    consult the index and would otherwise execute a configured fsmonitor
    #    hook — the gate itself must execute no helper (spec v1.4 §3/§5).
    stage_result = _git_run(
        project_dir, ["-c", "core.fsmonitor=", "ls-files", "--stage", "-z"]
    )
    if stage_result is None or isinstance(stage_result, _DecodeFailedProcess):
        return None
    if stage_result.returncode != 0:
        return None
    for record in stage_result.stdout.split("\0"):
        if not record:
            continue
        header, sep, _path = record.partition("\t")
        if not sep:
            return None  # malformed record: no TAB separator
        parts = header.split(" ")
        if len(parts) != 3:
            return None  # malformed header
        mode, sha, stage = parts
        if not _MODE_RE.fullmatch(mode) or not _SHA_RE.fullmatch(sha):
            return None  # malformed header
        if not _STAGE_RE.fullmatch(stage):
            return None  # malformed stage
        if mode == "160000":
            return None  # tracked gitlink -> UNEVALUABLE immediately

    # 2) TRACKED-PATH ENUMERATION
    list_result = _git_run(project_dir, ["-c", "core.fsmonitor=", "ls-files", "-z"])
    if list_result is None or isinstance(list_result, _DecodeFailedProcess):
        return None
    if list_result.returncode != 0:
        return None
    paths = [p for p in list_result.stdout.split("\0") if p != ""]
    if not paths:
        return "SAFE"  # no tracked paths can carry a filter

    # 3) BOUNDED ATTRIBUTE BATCHING
    batches: list[list[str]] = []
    cur: list[str] = []
    cur_bytes = 0
    for p in paths:
        b = len(p.encode("utf-8"))
        if cur and (len(cur) >= _MAX_PATHS_PER_BATCH or cur_bytes + b > _MAX_BYTES_PER_BATCH):
            batches.append(cur)
            cur = []
            cur_bytes = 0
        cur.append(p)
        cur_bytes += b
    if cur:
        batches.append(cur)

    drivers: set[str] = set()
    inspected: list[str] = []
    for batch in batches:
        attr_result = _git_run(
            project_dir,
            ["-c", "core.fsmonitor=", "check-attr", "-z", "filter", "--", *batch],
        )
        if attr_result is None or isinstance(attr_result, _DecodeFailedProcess):
            return None
        if attr_result.returncode != 0:
            return None
        fields = [f for f in attr_result.stdout.split("\0") if f != ""]
        if len(fields) % 3 != 0:
            return None  # malformed triple output
        for i in range(0, len(fields), 3):
            path, attr, value = fields[i], fields[i + 1], fields[i + 2]
            if attr != "filter":
                return None  # unexpected attribute field
            inspected.append(path)
            if value not in ("unspecified", "unset"):
                drivers.add(value)
    if inspected != paths:
        return None  # path-accounting mismatch: omission or duplication

    # 4) DRIVER CONFIGURATION INSPECTION
    for driver in sorted(drivers):
        for suffix in ("clean", "process", "required"):
            cfg_result = _git_run(
                project_dir,
                ["-c", "core.fsmonitor=", "config", "--get", f"filter.{driver}.{suffix}"],
            )
            if cfg_result is None or isinstance(cfg_result, _DecodeFailedProcess):
                return None
            if cfg_result.returncode == 0:
                value = cfg_result.stdout.strip()
                if suffix in ("clean", "process") and value:
                    return _HAZARD_UNSAFE
                if suffix == "required" and value not in ("true", "false"):
                    return None  # unexplained configuration shape
            elif cfg_result.returncode != 1:
                return None  # uncertain config query result

    return "SAFE"


def _probe_worktree_dirty(project_dir: Path) -> dict | None:
    """TSR v1.4: hazard gate first; git status only when SAFE.

    The read-only hazard gate (gitlink detection + bounded attribute/filter
    inspection) must positively establish that no execution-capable
    conversion/filter configuration applies before git status is reachable.
    If the gate is UNSAFE or UNEVALUABLE, the probe is unevaluable and
    git status MUST NOT be invoked (spec v1.4 §3/§3a/§3b).
    """
    hazard = _worktree_hazard_gate(project_dir)
    if hazard is None or hazard == _HAZARD_UNSAFE:
        return None  # UNEVALUABLE; git status NOT invoked
    # Both -c core.fsmonitor= and --no-optional-locks are GLOBAL git options
    # and must precede the subcommand. The -c override is command-local only.
    # The EMPTY value is the legacy-compatible fsmonitor suppression: Git
    # <=2.35.1 interprets non-empty values as hook command pathnames (so
    # "false" would be executed), while an empty value disables fsmonitor under
    # both legacy pathname semantics and modern boolean semantics.
    # --no-optional-locks suppresses the optional index refresh/lock
    # acquisition. Together they keep status side-effect-free (spec §8: no git
    # state mutations, no hidden execution).
    result = _git_run(
        project_dir,
        ["-c", "core.fsmonitor=", "--no-optional-locks", "status", "--porcelain"],
    )
    if result is None or result.returncode != 0:
        return None
    return {"dirty": bool(result.stdout)}


def _reconcile(project_dir: Path, recorded: dict) -> dict:
    """Evaluate all five probes and compute the reconciliation verdict."""
    live = {
        "repository_presence": _probe_repository_presence(project_dir),
        "head_sha": _probe_head_sha(project_dir),
        "branch": _probe_branch(project_dir),
        "upstream": _probe_upstream(project_dir),
        "worktree_dirty": _probe_worktree_dirty(project_dir),
    }

    probes: list[dict] = []
    reasons: list[str] = []
    for key in _PROBE_KEYS:
        rec = recorded.get(key)
        liv = live.get(key)
        evaluated = rec is not None and liv is not None
        matches = evaluated and rec == liv
        if evaluated and not matches:
            reasons.append(
                f"{key} differs: recorded {rec} != live {liv}"
            )
        probes.append(
            {
                "key": key,
                "recorded": rec,
                "live": liv,
                "evaluated": evaluated,
                "matches": matches,
            }
        )

    evaluated_any = any(p["evaluated"] for p in probes)
    if not evaluated_any:
        verdict = "UNKNOWN"
    elif any(p["evaluated"] and not p["matches"] for p in probes):
        verdict = "STALE"
    elif any(p["recorded"] is not None and p["live"] is None for p in probes):
        # TSR v1.4: a well-formed RECORDED probe with unevaluable live
        # observation prohibits CURRENT (spec v1.4 §4). Omitted and malformed
        # probes are excluded by the caller's recorded-value validation.
        verdict = "UNKNOWN"
    else:
        verdict = "CURRENT"
    return {"status": verdict, "probes": probes, "reasons": reasons}


def _render_json(result: dict) -> None:
    print(json.dumps(result, indent=2))


def _render_human(result: dict) -> None:
    print(f"Status: {result['status']}")
    print(f"State file: {result['state_file']}")
    for p in result["probes"]:
        print(
            f"  {p['key']}: recorded={p['recorded']} live={p['live']} "
            f"evaluated={p['evaluated']} matches={p['matches']}"
        )
    for reason in result["reasons"]:
        print(f"  reason: {reason}")
    doctor_health = result["doctor_health"]
    print(
        f"Doctor: {doctor_health['status']} "
        f"({len(doctor_health['findings'])} finding(s))"
    )


def status(project_path: str | None = None, json_output: bool = False) -> dict:
    """Reconcile recorded state against live git state (read-only).

    Returns the TSR v0 result dict per the frozen output contract (§7)
    and prints it as JSON or human-readable text.
    """
    project_dir = _resolve_project_dir(project_path)
    state_file = _state_file_path(project_dir)
    state_file_str = str(state_file) if state_file.is_file() else None

    usable = False
    recorded: dict = {}
    if state_file_str is not None:
        data = _load_state_file(state_file)
        if data is not None and data.get("schema_version") == SCHEMA_VERSION:
            usable = True
            raw_probes = data.get("probes")
            if isinstance(raw_probes, dict):
                for key in _PROBE_KEYS:
                    if key in raw_probes and _validate_recorded_probe(
                        key, raw_probes[key]
                    ):
                        recorded[key] = raw_probes[key]

    if state_file_str is None or not usable:
        # Fail-closed: no file, unparseable file, or wrong schema version.
        reconciliation = _reconcile(project_dir, {})
        reconciliation["status"] = "UNKNOWN"
        reconciliation["reasons"] = []
    else:
        reconciliation = _reconcile(project_dir, recorded)

    doctor_health = {"status": "not_run", "findings": []}
    try:
        doc = _doctor(
            project_path=str(project_dir),
            json_output=False,
            quiet=True,
            side_effect_free=True,
        )
        doctor_health = {
            "status": doc.get("status", "unhealthy"),
            "findings": doc.get("findings", []),
        }
    except Exception:
        doctor_health = {"status": "not_run", "findings": []}

    result = {
        "status": reconciliation["status"],
        "state_file": state_file_str,
        "schema_version": SCHEMA_VERSION,
        "probes": reconciliation["probes"],
        "doctor_health": doctor_health,
        "reasons": reconciliation["reasons"],
    }

    if json_output:
        _render_json(result)
    else:
        _render_human(result)
    return result
