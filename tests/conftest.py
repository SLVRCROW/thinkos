"""Shared test fixtures."""

import pytest
from thinkos.store.sqlite_store import SQLiteStore


@pytest.fixture
def store():
    s = SQLiteStore(":memory:")
    yield s
    s.close()
