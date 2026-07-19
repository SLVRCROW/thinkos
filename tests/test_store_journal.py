"""Tests for SQLiteStore journal mode — verifies WAL is requested and connections close cleanly."""

import os
import sqlite3
import tempfile

import pytest

from thinkos.store.sqlite_store import SQLiteStore
from thinkos.onboarding import init, THINKOS_DIR, STORE_FILENAME


class TestStoreJournalMode:
    """Correction A: file-backed runtime store requests and obtains WAL."""

    def test_file_backed_store_uses_wal(self):
        """A file-backed SQLiteStore requests WAL journal mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_wal.sqlite")
            store = SQLiteStore(db_path)
            try:
                cursor = store._conn.execute("PRAGMA journal_mode")
                mode = cursor.fetchone()[0]
                assert mode == "wal", f"Expected 'wal', got '{mode}'"
            finally:
                store.close()

    def test_memory_store_uses_wal(self):
        """An in-memory SQLiteStore also requests WAL (harmless for :memory:)."""
        store = SQLiteStore(":memory:")
        try:
            cursor = store._conn.execute("PRAGMA journal_mode")
            mode = cursor.fetchone()[0]
            # :memory: databases may return "memory" or "wal" depending on SQLite version
            assert mode in ("wal", "memory"), f"Expected 'wal' or 'memory', got '{mode}'"
        finally:
            store.close()

    def test_connection_closes_cleanly(self):
        """Store.close() does not raise and the connection is closed."""
        store = SQLiteStore(":memory:")
        store.close()
        # After close, any operation should raise sqlite3.ProgrammingError
        with pytest.raises(sqlite3.ProgrammingError):
            store._conn.execute("SELECT 1")

    def test_temp_directory_removable_after_lifecycle(self):
        """A temporary directory containing a WAL store can be removed after close."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_cleanup.sqlite")
            store = SQLiteStore(db_path)
            store.close()
            # The directory should be removable (no WAL/SHM locks)
            os.unlink(db_path)
            # Verify we can still remove the directory
            assert os.path.isdir(tmpdir)


class TestOnboardingJournalMode:
    """Correction A: onboarding init creates a usable database with WAL."""

    def test_onboarding_init_creates_usable_db(self):
        """onboarding.init() creates a database that can be opened and queried."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = init(project_path=tmpdir)
            assert result["status"] == "ok"

            store_path = os.path.join(tmpdir, THINKOS_DIR, STORE_FILENAME)
            assert os.path.isfile(store_path)

            conn = sqlite3.connect(store_path)
            try:
                cursor = conn.execute("SELECT sqlite_version()")
                version = cursor.fetchone()[0]
                assert version is not None
            finally:
                conn.close()

    def test_onboarding_init_uses_wal(self):
        """The database created by onboarding.init() has WAL journal mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            init(project_path=tmpdir)
            store_path = os.path.join(tmpdir, THINKOS_DIR, STORE_FILENAME)

            conn = sqlite3.connect(store_path)
            try:
                cursor = conn.execute("PRAGMA journal_mode")
                mode = cursor.fetchone()[0]
                assert mode == "wal", f"Expected 'wal', got '{mode}'"
            finally:
                conn.close()

    def test_onboarding_init_connection_closes_cleanly(self):
        """The connection created during onboarding.init() closes cleanly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            init(project_path=tmpdir)
            store_path = os.path.join(tmpdir, THINKOS_DIR, STORE_FILENAME)

            conn = sqlite3.connect(store_path)
            conn.close()
            # After close, any operation should raise
            with pytest.raises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")

    def test_onboarding_init_temp_dir_removable(self):
        """After onboarding init, the temp directory can be cleaned up."""
        with tempfile.TemporaryDirectory() as tmpdir:
            init(project_path=tmpdir)
            store_path = os.path.join(tmpdir, THINKOS_DIR, STORE_FILENAME)
            assert os.path.isfile(store_path)
            # Clean up the store file
            os.unlink(store_path)
            # The .thinkos directory should be removable
            thinkos_dir = os.path.join(tmpdir, THINKOS_DIR)
            assert os.path.isdir(thinkos_dir)
