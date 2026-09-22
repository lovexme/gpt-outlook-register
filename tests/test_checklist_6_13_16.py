import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def db(tmp_path, monkeypatch):
    import webui.db as module
    monkeypatch.setattr(module, "DB_PATH", tmp_path / "test.db")
    module.init_db()
    return module


def test_save_registered_preserves_credentials_and_extra_on_retry(db):
    email = "user@example.com"
    db.save_registered({
        "email": email, "password": "old-password", "access_token": "old-at",
        "session_token": "old-st", "refresh_token": "old-rt",
        "plus_check": {"status": "plus_active"}, "exit_ip": "1.2.3.4",
        "totp_secret": "secret",
    })
    db.save_registered({"email": email, "password": ""})
    row = db.get_registered(email)
    assert row["password"] == "old-password"
    assert row["access_token"] == "old-at"
    assert row["session_token"] == "old-st"
    assert row["refresh_token"] == "old-rt"
    assert row["extra"] == {
        "plus_check": {"status": "plus_active"}, "exit_ip": "1.2.3.4",
        "totp_secret": "secret",
    }


def test_extra_json_updates_are_serialized_and_merge(db):
    email = "user@example.com"
    db.save_registered({"email": email, "access_token": "at", "plus_check": {"status": "free"}})
    barrier = threading.Barrier(3)

    def update(payload):
        barrier.wait()
        db.set_extra_json(email, json.dumps(payload))

    threads = [
        threading.Thread(target=update, args=({"totp_secret": "secret"},)),
        threading.Thread(target=update, args=({"exit_ip": "5.6.7.8"},)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    db.update_plus_check(email, {"status": "plus_active"})
    extra = db.get_registered(email)["extra"]
    assert extra["totp_secret"] == "secret"
    assert extra["exit_ip"] == "5.6.7.8"
    assert extra["plus_check"] == {"status": "plus_active"}


def test_plan_filter_is_grouped_before_search(db):
    for email, status in (("plus-a@example.com", "plus_active"), ("plus-b@example.com", "free"), ("free-a@example.com", "free")):
        db.save_registered({"email": email, "access_token": "at", "plus_check": {"status": status}})
    assert db.count_registered("plus", "plus-a") == 1
    assert db.count_registered("plus", "free-a") == 0
    assert [r["email"] for r in db.list_registered(filter_rt="plus", search="plus-a")] == ["plus-a@example.com"]
