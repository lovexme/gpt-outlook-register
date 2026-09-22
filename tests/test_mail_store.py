import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from webui.mail_store import MailStore


@pytest.mark.parametrize("suffix", [".json", ".db"])
def test_remove_missing_email_returns_false(tmp_path, suffix):
    store = MailStore(str(tmp_path / f"mails{suffix}"))

    assert store.remove("missing@example.com") is False


def test_sqlite_configures_wal_and_busy_timeout(tmp_path):
    store = MailStore(str(tmp_path / "mails.db"))

    journal_mode = store._backend._conn.execute("PRAGMA journal_mode").fetchone()[0]  # noqa: SLF001
    busy_timeout = store._backend._conn.execute("PRAGMA busy_timeout").fetchone()[0]  # noqa: SLF001

    assert journal_mode.lower() == "wal"
    assert busy_timeout >= 10_000


def test_sqlite_concurrent_writes_are_serialized(tmp_path):
    path = str(tmp_path / "mails.db")
    workers = 12
    records_per_worker = 25
    barrier = threading.Barrier(workers)
    errors = []

    def write_records(worker_id):
        try:
            store = MailStore(path)
            records = [
                {"email": f"user-{worker_id}-{index}@example.com"}
                for index in range(records_per_worker)
            ]
            barrier.wait()
            assert store.add_many(records) == records_per_worker
        except BaseException as exc:  # report worker failures in the test thread
            errors.append(exc)

    threads = [threading.Thread(target=write_records, args=(i,)) for i in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert MailStore(path).count() == workers * records_per_worker
