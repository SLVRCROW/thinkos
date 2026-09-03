"""Frozen acceptance tests for TSR v0 — thinkos status (spec §9, tests 1-20 plus
two fail-closed edge tests 18b/19).

Contract: docs/specs/TSR_V0_SPEC_v1.3.md (controlling, adopted 2026-09-02).
v1.1 remains the prior frozen record. Tests are platform-neutral
(Test 11 uses pathlib on a temp git repo with no Windows-specific assertion).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from thinkos.status import status

GIT_ENV = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}


def _git_env():
    env = os.environ.copy()
    env.update(GIT_ENV)
    return env


def _git(repo: Path, *args: str):
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        env=_git_env(),
        capture_output=True,
        text=True,
    )


def _commit(repo: Path, message: str = "init"):
    r = _git(
        repo,
        "-c",
        "user.name=tsr-test",
        "-c",
        "user.email=tsr-test@example.com",
        "commit",
        "--allow-empty",
        "-m",
        message,
    )
    assert r.returncode == 0, r.stderr


def _make_repo(tmp_path: Path, name: str = "repo") -> Path:
    """Create a plain git repo (NO synthetic root .gitignore).

    v1.3: fixtures must instantiate the same shape that real `thinkos init`
    actually produces (nested .thinkos/.gitignore + committed runtime config;
    project-state.json as ignored operational state). The old root
    `.thinkos/` ignore rule was scaffolding the product never produces and
    contradicted the v1.2 guardrail; it is gone.
    """
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _commit(repo, "init")
    return repo


def _init_thinkos_project(project: Path) -> Path:
    """Run REAL `thinkos init` and commit the runtime config once.

    This is the product shape (v1.3): .thinkos/thinkos.json + nested
    .thinkos/.gitignore are committed project config; the DB and
    project-state.json are ignored operational state. Returns the store.
    """
    env = _git_env()
    env["PYTHONPATH"] = str(_REPO_ROOT)
    r = subprocess.run(
        [sys.executable, "-m", "thinkos", "init", "--json", str(project)],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    # Commit the runtime config (nested .gitignore + thinkos.json) once.
    _git(project, "add", ".thinkos/thinkos.json", ".thinkos/.gitignore")
    _commit(project, "add thinkos runtime config")
    store = project / ".thinkos" / "thinkos.sqlite"
    assert store.is_file()
    return store


def _make_bare_remote(tmp_path: Path, name: str = "remote.git") -> Path:
    remote = tmp_path / name
    remote.mkdir()
    _git(remote, "init", "--bare")
    return remote


def _add_upstream(repo: Path, remote: Path):
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-u", "origin", "main")


def _sha(repo: Path) -> str:
    r = _git(repo, "rev-parse", "HEAD")
    assert r.returncode == 0
    return r.stdout.strip()


def _state_dir(repo: Path) -> Path:
    return repo / ".thinkos"


def _write_state(repo: Path, payload: dict):
    d = _state_dir(repo)
    d.mkdir(exist_ok=True)
    (d / "project-state.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


def _good_state(repo: Path, head_sha: str | None = None) -> dict:
    if head_sha is None:
        head_sha = _sha(repo)
    return {
        "schema_version": "tsr.v0",
        "recorded_at": "2026-08-20T00:00:00Z",
        "probes": {
            "repository_presence": {"exists": True},
            "head_sha": {"value": head_sha},
            "branch": {"detached": False, "branch": "main"},
            "upstream": {"configured": False},
            "worktree_dirty": {"dirty": False},
        },
    }


_REPO_ROOT = Path(__file__).resolve().parent.parent


def _cli_status(repo: Path, *extra: str):
    env = _git_env()
    env["PYTHONPATH"] = str(_REPO_ROOT)
    return subprocess.run(
        [sys.executable, "-m", "thinkos", "status", "--json", str(repo), *extra],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
    )


def _tree(root: Path) -> dict:
    """Relative path -> file bytes, for all files under root."""
    snapshot = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            snapshot[str(p.relative_to(root))] = p.read_bytes()
    return snapshot


# ── Test 1: No .thinkos/ → UNKNOWN, exit 2, no files created ─────────────
def test_01_no_thinkos_dir_unknown_exit2_no_files_created(tmp_path):
    project = tmp_path / "plain"
    project.mkdir()
    before = _tree(project)
    r = _cli_status(project)
    assert r.returncode == 2
    result = json.loads(r.stdout)
    assert result["status"] == "UNKNOWN"
    assert result["state_file"] is None
    assert _tree(project) == before


# ── Test 2: initialized project, no state file → UNKNOWN, exit 2 ─────────
def test_02_initialized_no_state_file_unknown(tmp_path):
    repo = _make_repo(tmp_path)
    assert not _state_dir(repo).exists()
    r = _cli_status(repo)
    assert r.returncode == 2
    result = json.loads(r.stdout)
    assert result["status"] == "UNKNOWN"
    assert result["state_file"] is None
    # status must not create anything
    assert not _state_dir(repo).exists()


# ── 3: matching file → CURRENT exit 0; unhealthy doctor reported, no effect ──
def test_03_current_doctor_unhealthy_does_not_change_status(tmp_path):
    repo = _make_repo(tmp_path)
    state = _good_state(repo)
    # Plain repo (no thinkos init): the uncommitted state file makes the
    # worktree genuinely dirty — record it honestly so the verdict is CURRENT
    # while doctor is unhealthy (no .thinkos config → unhealthy).
    state["probes"]["worktree_dirty"] = {"dirty": True}
    _write_state(repo, state)
    r = _cli_status(repo)
    assert r.returncode == 0
    result = json.loads(r.stdout)
    assert result["status"] == "CURRENT"
    assert result["schema_version"] == "tsr.v0"
    assert result["doctor_health"]["status"] == "unhealthy"
    for p in result["probes"]:
        assert p["evaluated"] is True
        assert p["matches"] is True
    assert result["reasons"] == []


# ── 4: live change without file update → STALE, exit 1, per-probe reasons ──
def test_04_live_changes_stale_exit1_per_probe_reasons(tmp_path):
    repo = _make_repo(tmp_path)
    remote = _make_bare_remote(tmp_path)
    _add_upstream(repo, remote)
    state = _good_state(repo)
    state["probes"]["upstream"] = {
        "configured": True,
        "ref": "origin/main",
        "sha": _sha(repo),
    }
    _write_state(repo, state)

    # Live changes WITHOUT updating the state file:
    _commit(repo, "c2")                       # head_sha changes
    _git(repo, "checkout", "-b", "feature")   # branch changes, upstream unset
    (repo / "dirty.txt").write_text("x")      # worktree becomes dirty
    r = _cli_status(repo)
    assert r.returncode == 1
    result = json.loads(r.stdout)
    assert result["status"] == "STALE"
    differing = [p["key"] for p in result["probes"] if p["evaluated"] and not p["matches"]]
    assert differing == ["head_sha", "branch", "upstream", "worktree_dirty"]
    # one reason string per differing evaluated probe
    assert len(result["reasons"]) == len(differing)
    for key in differing:
        assert any(key in reason for reason in result["reasons"])


# ── 5: malformed JSON → UNKNOWN, exit 2 ───────────────────────────────────
def test_05_malformed_json_unknown_exit2(tmp_path):
    repo = _make_repo(tmp_path)
    d = _state_dir(repo)
    d.mkdir()
    (d / "project-state.json").write_text("{not json!!", encoding="utf-8")
    r = _cli_status(repo)
    assert r.returncode == 2
    result = json.loads(r.stdout)
    assert result["status"] == "UNKNOWN"
    assert result["state_file"] == str(d / "project-state.json")


# ── 6: wrong schema_version → UNKNOWN, exit 2 ───────────────────────────
def test_06_wrong_schema_version_unknown_exit2(tmp_path):
    repo = _make_repo(tmp_path)
    state = _good_state(repo)
    state["schema_version"] = "tsr.v9"
    _write_state(repo, state)
    r = _cli_status(repo)
    assert r.returncode == 2
    result = json.loads(r.stdout)
    assert result["status"] == "UNKNOWN"


# ── 7: determinism — 5 runs → identical output ───────────────────────────
def test_07_determinism_five_runs_identical(tmp_path):
    repo = _make_repo(tmp_path)
    state = _good_state(repo)
    state["probes"]["worktree_dirty"] = {"dirty": True}  # honest (uncommitted state)
    _write_state(repo, state)
    outputs = [json.dumps(status(str(repo), json_output=True)) for _ in range(5)]
    assert len(set(outputs)) == 1


# ── 8: side-effect-free — repeated runs → directory tree unchanged ───────
def test_08_side_effect_free_tree_unchanged(tmp_path):
    repo = _make_repo(tmp_path)
    _write_state(repo, _good_state(repo))
    before = _tree(repo)
    for _ in range(3):
        status(str(repo), json_output=True)
    assert _tree(repo) == before


# ── 9: git unavailable → probes non-evaluated → UNKNOWN, exit 2 ──────────
def test_09_git_unavailable_non_evaluated_unknown(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    _write_state(repo, _good_state(repo))
    empty_path = tmp_path / "empty-bin"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))
    result = status(str(repo), json_output=True)
    assert result["status"] == "UNKNOWN"
    for p in result["probes"]:
        assert p["evaluated"] is False
        assert p["live"] is None


# ── 10: non-repo dir with recorded repository_presence:true → STALE ──────
def test_10_non_repo_recorded_presence_true_stale(tmp_path):
    project = tmp_path / "not-a-repo"
    project.mkdir()
    state = {
        "schema_version": "tsr.v0",
        "recorded_at": "2026-08-20T00:00:00Z",
        "probes": {
            "repository_presence": {"exists": True},
            "head_sha": {"value": "0" * 40},
            "branch": {"detached": False, "branch": "main"},
            "upstream": {"configured": False},
            "worktree_dirty": {"dirty": False},
        },
    }
    _write_state(project, state)
    r = _cli_status(project)
    assert r.returncode == 1
    result = json.loads(r.stdout)
    assert result["status"] == "STALE"
    presence = next(p for p in result["probes"] if p["key"] == "repository_presence")
    assert presence["recorded"] == {"exists": True}
    assert presence["live"] == {"exists": False}
    assert presence["evaluated"] is True
    assert presence["matches"] is False


# ── 11: platform-neutral smoke on a temp git repo via pathlib ─────────────
def test_11_platform_neutral_smoke(tmp_path):
    repo = _make_repo(tmp_path)
    _write_state(repo, _good_state(repo))
    result = status(str(repo), json_output=True)
    assert result["status"] in ("CURRENT", "STALE", "UNKNOWN")
    assert result["schema_version"] == "tsr.v0"
    assert [p["key"] for p in result["probes"]] == [
        "repository_presence",
        "head_sha",
        "branch",
        "upstream",
        "worktree_dirty",
    ]
    assert isinstance(result["doctor_health"]["findings"], list)


# ── 12: omitted probe subset → only present probes evaluated ─────────────
def test_12_omitted_probe_subset(tmp_path):
    repo = _make_repo(tmp_path)
    state = _good_state(repo)
    del state["probes"]["head_sha"]
    del state["probes"]["branch"]
    del state["probes"]["upstream"]
    state["probes"]["worktree_dirty"] = {"dirty": True}  # honest (uncommitted state)
    _write_state(repo, state)
    result = status(str(repo), json_output=True)
    assert result["status"] == "CURRENT"
    by_key = {p["key"]: p for p in result["probes"]}
    assert by_key["repository_presence"]["evaluated"] is True
    assert by_key["worktree_dirty"]["evaluated"] is True
    assert by_key["head_sha"]["evaluated"] is False
    assert by_key["branch"]["evaluated"] is False
    assert by_key["upstream"]["evaluated"] is False
    assert result["reasons"] == []


def test_12b_omitted_probe_subset_stale_when_present_probe_differs(tmp_path):
    repo = _make_repo(tmp_path)
    state = _good_state(repo)
    del state["probes"]["head_sha"]
    del state["probes"]["branch"]
    del state["probes"]["upstream"]
    state["probes"]["worktree_dirty"] = {"dirty": False}
    _write_state(repo, state)
    (repo / "new.txt").write_text("n", encoding="utf-8")  # live dirty: true
    result = status(str(repo), json_output=True)
    assert result["status"] == "STALE"
    assert len(result["reasons"]) == 1
    assert "worktree_dirty" in result["reasons"][0]


# ── 13: malformed probe value → that probe excluded, others evaluate ─────
def test_13_malformed_probe_excluded_others_evaluate(tmp_path):
    repo = _make_repo(tmp_path)
    state = _good_state(repo)
    state["probes"]["head_sha"] = {"value": "abc"}  # not 40-hex → malformed
    state["probes"]["worktree_dirty"] = {"dirty": True}  # honest (uncommitted state)
    _write_state(repo, state)
    result = status(str(repo), json_output=True)
    assert result["status"] == "CURRENT"
    by_key = {p["key"]: p for p in result["probes"]}
    assert by_key["head_sha"]["evaluated"] is False
    assert by_key["repository_presence"]["evaluated"] is True
    assert by_key["branch"]["evaluated"] is True
    assert by_key["worktree_dirty"]["evaluated"] is True


# ── 14 (v1.3, inverted): operational-state guardrail ──────────────────────
# Real `thinkos init` writes a nested .thinkos/.gitignore that ignores
# project-state.json (repo-local operational state, never committed) AND the
# runtime DB files. git check-ignore must confirm both.
def test_14_gitignore_protects_operational_state_and_db(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    _init_thinkos_project(repo)
    # sandbox the in-process git calls against global gitignore rules
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    # state file must be IGNORED (operational, not tracked)
    r = _git(repo, "check-ignore", "-q", ".thinkos/project-state.json")
    assert r.returncode == 0, f"state file not ignored: {r.stdout} {r.stderr}"
    # runtime DB files must remain ignored
    for name in ("thinkos.sqlite", "thinkos.sqlite-wal", "thinkos.sqlite-shm"):
        r = _git(repo, "check-ignore", "-q", f".thinkos/{name}")
        assert r.returncode == 0, f"{name} not ignored"
    # and the state file still reconciles (honest: worktree clean after config commit)
    _write_state(repo, _good_state(repo))
    result = status(str(repo), json_output=True)
    assert result["status"] == "CURRENT"


# ── Containment regressions (Marc-authorized, 2026-08-20) ─────────────────
# R1: non-UTF-8 / unparseable state file → fail-closed UNKNOWN, exit 2,
#     contract-compliant JSON output (spec §2/§6/§7). No traceback.
def test_15_non_utf8_state_file_fails_closed_unknown_exit2(tmp_path):
    repo = _make_repo(tmp_path)
    d = _state_dir(repo)
    d.mkdir()
    (d / "project-state.json").write_bytes(b"\xff\xfe\x00garbage-bytes")
    r = _cli_status(repo)
    assert r.returncode == 2
    assert r.stderr == ""
    result = json.loads(r.stdout)
    assert result["status"] == "UNKNOWN"
    assert result["state_file"] == str(d / "project-state.json")
    assert result["reasons"] == []


# 16: filesystem immutability on a REAL initialized ThinkOS project —
#     status creates/modifies/deletes ZERO filesystem objects, including
#     WAL/SHM files (spec §8; regression for the doctor WAL/SHM side-effect).
def test_16_status_zero_fs_objects_created_with_real_initialized_project(tmp_path):
    repo = _make_repo(tmp_path)
    _init_thinkos_project(repo)
    _write_state(repo, _good_state(repo))
    store = repo / ".thinkos" / "thinkos.sqlite"
    store.write_bytes(store.read_bytes())  # touch mtime, keep bytes identical

    before = _tree(repo)
    for _ in range(3):
        r = _cli_status(repo)
        assert r.returncode in (0, 1, 2)
        assert json.loads(r.stdout)["status"] in ("CURRENT", "STALE", "UNKNOWN")
    after = _tree(repo)

    # Nothing may be created, modified, or deleted — including WAL/SHM.
    assert before == after, (
        "status mutated the filesystem; "
        f"created={sorted(set(after) - set(before))} "
        f"deleted={sorted(set(before) - set(after))} "
        f"modified={sorted(k for k in set(before) & set(after) if before[k] != after[k])}"
    )
    # Belt-and-braces: no WAL/SHM files may exist after status runs.
    assert not (repo / ".thinkos" / "thinkos.sqlite-wal").exists()
    assert not (repo / ".thinkos" / "thinkos.sqlite-shm").exists()


# R2 (post-review): pathologically nested JSON must also fail closed with
# contract output and no traceback (RecursionError escape found by review).
def test_17_deeply_nested_json_fails_closed_unknown_exit2(tmp_path):
    repo = _make_repo(tmp_path)
    d = _state_dir(repo)
    d.mkdir()
    depth = 20000
    (d / "project-state.json").write_text(
        "[" * depth + "1" + "]" * depth, encoding="utf-8"
    )
    r = _cli_status(repo)
    assert r.returncode == 2
    assert r.stderr == ""
    result = json.loads(r.stdout)
    assert result["status"] == "UNKNOWN"
    assert result["state_file"] == str(d / "project-state.json")


# ── R3 (final-gate adversarial, 2026-08-20): non-UTF-8 GIT OUTPUT ──────────
# A real Git ref containing invalid UTF-8 bytes makes `git symbolic-ref
# --short HEAD` emit undecodable output. Spec §6 fail-closed: the branch
# probe is unevaluable; no state file → zero evaluated → UNKNOWN, exit 2,
# contract JSON, empty stderr, no traceback, zero filesystem mutation.
def _make_repo_with_invalid_utf8_branch(tmp_path: Path) -> Path:
    repo = _make_repo(tmp_path)
    sha = _sha(repo)
    bad = b"caf\xe9"  # invalid UTF-8 byte sequence in ref name
    # surrogateescape passes the RAW byte through the subprocess arg list;
    # latin-1 would re-encode to valid UTF-8 and defeat the test.
    bad_name = bad.decode("utf-8", "surrogateescape")
    r = _git(repo, "update-ref", "refs/heads/" + bad_name, sha)
    assert r.returncode == 0, r.stderr
    r = _git(repo, "symbolic-ref", "HEAD", "refs/heads/" + bad_name)
    assert r.returncode == 0, r.stderr
    # Prove the ref really contains the invalid byte on disk (raw bytes
    # capture — the text-mode helper would crash decoding it).
    r = subprocess.run(
        ["git", "symbolic-ref", "-q", "--short", "HEAD"],
        cwd=str(repo),
        env=_git_env(),
        capture_output=True,
    )
    assert r.returncode == 0, r.stderr
    assert b"\xe9" in r.stdout, r.stdout
    return repo


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="real-substrate invalid-UTF-8 Git ref fixture proven only on Linux; "
    "Windows Git normalizes invalid bytes (\\xe9 -> \\xef\\xbf\\xbd) before product code runs; "
    "semantic invariant covered portably by test_18c_*",
)
def test_18_non_utf8_git_output_fails_closed_unknown_exit2(tmp_path):
    repo = _make_repo_with_invalid_utf8_branch(tmp_path)
    before = _tree(repo)
    r = _cli_status(repo)
    assert r.returncode == 2
    assert r.stderr == ""
    result = json.loads(r.stdout)
    assert result["status"] == "UNKNOWN"
    branch = next(p for p in result["probes"] if p["key"] == "branch")
    assert branch["evaluated"] is False
    assert branch["live"] is None
    assert _tree(repo) == before


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="real-substrate invalid-UTF-8 Git ref fixture proven only on Linux; "
    "Windows Git normalizes invalid bytes (\\xe9 -> \\xef\\xbf\\xbd) before product code runs; "
    "semantic invariant covered portably by test_18c_*",
)
def test_18b_non_utf8_git_output_with_valid_state_still_fails_closed(tmp_path):
    # With a state file present but the branch probe unevaluable, the
    # remaining evaluated probes may be CURRENT — but the branch probe must
    # NEVER be misreported as detached or matched on corrupted bytes.
    repo = _make_repo_with_invalid_utf8_branch(tmp_path)
    state = _good_state(repo)
    state["probes"]["branch"] = {"detached": False, "branch": "main"}
    _write_state(repo, state)
    r = _cli_status(repo)
    assert r.returncode in (0, 1, 2)  # remaining probes decide
    assert r.stderr == ""
    result = json.loads(r.stdout)
    branch = next(p for p in result["probes"] if p["key"] == "branch")
    assert branch["evaluated"] is False
    assert branch["live"] is None
    assert branch["recorded"] == {"detached": False, "branch": "main"}
    assert branch["matches"] is False


# ── R4 (final-gate adversarial, 2026-08-20): constrained-memory fail-closed ──
# An unparseable-under-resources state file (MemoryError in read or parse)
# must fail closed to UNKNOWN, exit 2, contract JSON, empty stderr, no
# traceback. Regression runs in a subprocess under a tight address-space
# ulimit so the MemoryError is real, not mocked.
@pytest.mark.skipif(
    sys.platform != "linux",
    reason="real-substrate ulimit -v subprocess fixture proven only on Linux; "
    "native Windows routes bash to the WSL stub before product code runs; "
    "semantic invariant covered portably by test_19b",
)
def test_19_memory_error_fails_closed_unknown_exit2(tmp_path):
    repo = _make_repo(tmp_path)
    d = _state_dir(repo)
    d.mkdir()
    huge = "[" * (200 * 1024 * 1024)
    (d / "project-state.json").write_text(huge, encoding="utf-8")
    before = _tree(repo)
    env = _git_env()
    env["PYTHONPATH"] = str(_REPO_ROOT)
    r = subprocess.run(
        ["bash", "-c", f'ulimit -v 65536; exec "{sys.executable}" -m thinkos status --json "{repo}"'],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 2
    assert r.stderr == ""
    result = json.loads(r.stdout)
    assert result["status"] == "UNKNOWN"
    assert _tree(repo) == before


# ── 20 (v1.3): lifecycle regression — CURRENT reachable & stable ──────────
# Real product lifecycle on the REAL init shape: git init → thinkos init
# (config committed once) → record state → commit real work → re-record
# state → status CURRENT, stable across repeated runs, worktree clean.
# Proves the state-lifecycle contradiction is resolved (spec §9 t20).
def test_20_lifecycle_current_reachable_and_stable(tmp_path):
    repo = _make_repo(tmp_path)
    _init_thinkos_project(repo)

    # T1: record state against current reality (worktree clean: config committed)
    _write_state(repo, _good_state(repo))
    r = _cli_status(repo)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["status"] == "CURRENT"
    assert _git(repo, "status", "--porcelain").stdout == ""

    # T2: real work commit moves HEAD → STALE on head_sha
    (repo / "work.txt").write_text("work", encoding="utf-8")
    _git(repo, "add", "work.txt")
    _commit(repo, "real work")
    r = _cli_status(repo)
    assert r.returncode == 1, r.stderr
    assert json.loads(r.stdout)["status"] == "STALE"

    # T3: re-record state (ignored file — NO commit needed, NO self-reference)
    _write_state(repo, _good_state(repo))
    r = _cli_status(repo)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["status"] == "CURRENT"

    # T4: stability + clean worktree (state never committed)
    for _ in range(3):
        r = _cli_status(repo)
        assert r.returncode == 0, r.stderr
        assert json.loads(r.stdout)["status"] == "CURRENT"
    assert _git(repo, "status", "--porcelain").stdout == ""


# ── Portable fail-closed coverage (2026-09-03, Windows CI repair) ─────────
# The real-substrate fixtures test_18/18b/19 are proven only on Linux
# (Windows Git normalizes invalid bytes; bash ulimit routes to the WSL stub).
# These portable tests exercise the SAME product seams in-process on every
# platform: targeted _git_run injection for undecodable branch output, and
# targeted Path.read_text injection for MemoryError inside the real
# _load_state_file execution. No mock replaces status(), _reconcile(), or the
# branch probe itself. No product code changes.
def _run_status_in_process(repo: Path, capsys) -> int:
    """Invoke the real CLI dispatcher in-process; return the SystemExit code."""
    import thinkos.__main__ as main

    old_argv = sys.argv
    sys.argv = ["thinkos", "status", str(repo), "--json"]
    try:
        with pytest.raises(SystemExit) as exc:
            main._run_status()
        return exc.value.code
    finally:
        sys.argv = old_argv


def _branch_only_decode_failure(monkeypatch, repo: Path):
    """Targeted _git_run wrapper: fail decode ONLY for the branch command."""
    import thinkos.status as status_mod

    original = status_mod._git_run

    def wrapped(project_dir, args):
        if args == ["symbolic-ref", "-q", "--short", "HEAD"]:
            return status_mod._DecodeFailedProcess()
        return original(project_dir, args)

    monkeypatch.setattr(status_mod, "_git_run", wrapped)


def test_18c_undecodable_branch_without_state_fails_closed_unknown_exit2(
    tmp_path, monkeypatch, capsys
):
    # Case A: undecodable branch output + no usable state → branch unevaluable
    # → UNKNOWN / exit 2 / no traceback / no filesystem mutation.
    repo = _make_repo(tmp_path)
    _branch_only_decode_failure(monkeypatch, repo)
    before = _tree(repo)

    code = _run_status_in_process(repo, capsys)

    assert code == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    result = json.loads(captured.out)
    assert result["status"] == "UNKNOWN"
    branch = next(p for p in result["probes"] if p["key"] == "branch")
    assert branch["evaluated"] is False
    assert branch["live"] is None
    assert _tree(repo) == before


def test_18c_undecodable_branch_with_valid_state_never_misreported(
    tmp_path, monkeypatch, capsys
):
    # Case B: undecodable branch output + valid recorded state → branch
    # unevaluable, NEVER misreported as detached or matched on corrupted
    # bytes; other probes remain free to determine the overall status.
    repo = _make_repo(tmp_path)
    state = _good_state(repo)
    state["probes"]["branch"] = {"detached": False, "branch": "main"}
    _write_state(repo, state)
    _branch_only_decode_failure(monkeypatch, repo)
    before = _tree(repo)

    code = _run_status_in_process(repo, capsys)

    assert code in (0, 1, 2)  # remaining probes decide
    captured = capsys.readouterr()
    assert captured.err == ""
    result = json.loads(captured.out)
    branch = next(p for p in result["probes"] if p["key"] == "branch")
    assert branch["recorded"] == {"detached": False, "branch": "main"}
    assert branch["live"] is None
    assert branch["evaluated"] is False
    assert branch["matches"] is False
    assert _tree(repo) == before


def test_19b_memory_error_in_state_load_fails_closed_unknown_exit2(
    tmp_path, monkeypatch, capsys
):
    # MemoryError INSIDE the real _load_state_file execution (at the
    # Path.read_text seam) → UNKNOWN / exit 2 / no traceback / no mutation.
    # _load_state_file itself is NOT monkeypatched; the real except
    # (OSError, UnicodeDecodeError, MemoryError) block must contain it.
    repo = _make_repo(tmp_path)
    d = _state_dir(repo)
    d.mkdir()
    target = d / "project-state.json"
    target.write_text(json.dumps(_good_state(repo)), encoding="utf-8")
    before = _tree(repo)

    original_read_text = Path.read_text

    def targeted_read_text(self, *args, **kwargs):
        if str(self) == str(target):
            raise MemoryError("simulated constrained-memory read")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", targeted_read_text)

    code = _run_status_in_process(repo, capsys)

    assert code == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    result = json.loads(captured.out)
    assert result["status"] == "UNKNOWN"
    assert _tree(repo) == before


# ── Branch-probe launch-failure fail-closed repair (2026-09-03) ────────────
# Distinction (TSR v0 §3/§6):
#   A. symbolic-ref CANNOT LAUNCH (_git_run returns None) -> probe UNEVALUABLE
#   B. symbolic-ref LAUNCHES with a DEFINED nonzero exit and rev-parse HEAD
#      succeeds -> probe DETACHED
# Case A must NOT fall through to rev-parse HEAD and misreport detached.
def _branch_probe_returns(monkeypatch, symref_result):
    """Targeted _git_run wrapper: branch command returns symref_result;
    delegate ALL other git calls to the original _git_run."""
    import thinkos.status as status_mod

    original = status_mod._git_run

    def wrapped(project_dir, args):
        if args == ["symbolic-ref", "-q", "--short", "HEAD"]:
            return symref_result
        return original(project_dir, args)

    monkeypatch.setattr(status_mod, "_git_run", wrapped)


def _write_branch_only_state(repo: Path):
    d = _state_dir(repo)
    d.mkdir(exist_ok=True)
    (d / "project-state.json").write_text(
        json.dumps(
            {
                "schema_version": "tsr.v0",
                "recorded_at": "2026-09-03T00:00:00Z",
                "probes": {
                    "branch": {"detached": False, "branch": "main"},
                },
            }
        ),
        encoding="utf-8",
    )


def test_21_branch_launch_failure_fails_closed_unknown_exit2(
    tmp_path, monkeypatch, capsys
):
    # Case A: symbolic-ref CANNOT LAUNCH (None) -> probe UNEVALUABLE, NOT
    # detached; valid state with only the branch probe -> zero evaluated
    # probes -> UNKNOWN / exit 2 / reasons [] / no mutation.
    repo = _make_repo(tmp_path)  # real attached "main" repository
    _write_branch_only_state(repo)
    _branch_probe_returns(monkeypatch, None)
    before = _tree(repo)

    code = _run_status_in_process(repo, capsys)

    assert code == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    result = json.loads(captured.out)
    assert result["status"] == "UNKNOWN"
    assert result["reasons"] == []
    branch = next(p for p in result["probes"] if p["key"] == "branch")
    assert branch["recorded"] == {"detached": False, "branch": "main"}
    assert branch["live"] is None
    assert branch["evaluated"] is False
    assert branch["matches"] is False
    assert _tree(repo) == before


def test_22_defined_branch_failure_remains_detached_stale_exit1(
    tmp_path, monkeypatch, capsys
):
    # Case B: symbolic-ref LAUNCHES with a DEFINED returncode != 0 and
    # rev-parse HEAD succeeds -> probe DETACHED (TSR v0 §3 preserved).
    # Recorded state says attached main -> evaluated True, matches False
    # -> STALE / exit 1.
    repo = _make_repo(tmp_path)
    _write_branch_only_state(repo)
    defined_failure = subprocess.CompletedProcess(
        args=["git", "symbolic-ref", "-q", "--short", "HEAD"],
        returncode=1,
        stdout="",
        stderr="",
    )
    _branch_probe_returns(monkeypatch, defined_failure)
    before = _tree(repo)

    code = _run_status_in_process(repo, capsys)

    assert code == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    result = json.loads(captured.out)
    assert result["status"] == "STALE"
    branch = next(p for p in result["probes"] if p["key"] == "branch")
    assert branch["live"] == {"detached": True, "branch": None}
    assert branch["recorded"] == {"detached": False, "branch": "main"}
    assert branch["evaluated"] is True
    assert branch["matches"] is False
    assert _tree(repo) == before
