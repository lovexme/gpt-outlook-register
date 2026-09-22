import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_run_exports_without_rt_uses_registration_proxy_and_does_not_upload_after_session_failure(monkeypatch):
    from webui import exporter

    calls = []

    def session_fail(cred, *, proxy=""):
        calls.append(("session", proxy))
        raise RuntimeError("mock session failure")

    def should_not_upload(*args, **kwargs):
        raise AssertionError("upload must not be called")

    monkeypatch.setattr(exporter, "_fetch_chatgpt_session_access_token", session_fail)
    monkeypatch.setattr(exporter, "export_to_cpa", should_not_upload)
    monkeypatch.setattr(exporter, "export_to_sub2api", should_not_upload)

    result = exporter.run_exports(
        {"email": "a@example.com", "access_token": "old-at", "refresh_token": ""},
        cpa_cfg={"enabled": True},
        sub2api_cfg={"enabled": True},
        proxy="socks5://registration-proxy:1080",
    )

    assert calls == [("session", "socks5://registration-proxy:1080")]
    assert result["cpa"]["ok"] is False
    assert result["sub2api"]["ok"] is False
    assert "session" in result["cpa"]["error"]
    assert "session" in result["sub2api"]["error"]


def test_run_exports_with_rt_does_not_rotate_and_http_200_is_accepted(monkeypatch):
    from webui import exporter

    def fail_rotation(*args, **kwargs):
        raise AssertionError("refresh token rotation must not be called")

    monkeypatch.setattr(exporter, "refresh_codex_token", fail_rotation)
    monkeypatch.setattr(exporter, "export_to_cpa", lambda cred, cfg, **kw: {
        "ok": True, "status": "accepted", "email": cred["email"]
    })

    result = exporter.run_exports(
        {"email": "a@example.com", "access_token": "at", "refresh_token": "rt"},
        cpa_cfg={"enabled": True},
    )

    assert result["cpa"]["status"] == "accepted"


def test_registrar_auto_export_passes_registration_proxy(monkeypatch):
    from webui import registrar

    captured = {}
    monkeypatch.setattr(registrar.db, "get_export_internal_config", lambda: {
        "cpa": {"enabled": True}, "sub2api": {"enabled": False},
        "chatgpt2api": {"enabled": False},
    })
    monkeypatch.setattr(registrar.exporter if hasattr(registrar, "exporter") else __import__("webui.exporter", fromlist=["exporter"]), "run_exports", lambda *args, **kwargs: captured.update(kwargs) or {"cpa": {"ok": True}})
    monkeypatch.setattr(registrar, "_emit_status", lambda *args, **kwargs: None)

    registrar._try_export_to_panels("run", {"email": "a@example.com"}, proxy="http://reg-proxy:1")
    assert captured["proxy"] == "http://reg-proxy:1"


def test_push_selected_reports_account_status_and_by_target(monkeypatch):
    from webui import app

    creds = {
        "one@example.com": {"email": "one@example.com"},
        "two@example.com": {"email": "two@example.com"},
        "three@example.com": {"email": "three@example.com"},
    }
    monkeypatch.setattr(app.db, "get_export_internal_config", lambda: {
        "cpa": {"enabled": True}, "sub2api": {"enabled": True},
        "chatgpt2api": {"enabled": False},
    })
    monkeypatch.setattr(app.db, "get_registered", lambda email: creds.get(email))

    def fake_run_exports(cred, **kwargs):
        if cred["email"].startswith("one"):
            return {"cpa": {"ok": True, "status": "accepted"},
                    "sub2api": {"ok": True, "status": "accepted"}}
        if cred["email"].startswith("two"):
            return {"cpa": {"ok": True, "status": "accepted"},
                    "sub2api": {"ok": False, "error": "mock target failure"}}
        return {"cpa": {"ok": False, "error": "mock target failure"},
                "sub2api": {"ok": False, "error": "mock target failure"}}

    monkeypatch.setattr(app, "_exp", None, raising=False)
    import webui.exporter as exporter
    monkeypatch.setattr(exporter, "run_exports", fake_run_exports)

    response = app.api_push_selected(app.PushSelectedReq(emails=list(creds)))

    assert response["all_success"] == 1
    assert response["partial_success"] == 1
    assert response["all_failed"] == 1
    assert response["by_target"]["cpa"] == {"accepted": 2, "failed": 1}
    assert response["by_target"]["sub2api"] == {"accepted": 1, "failed": 2}
    assert response["results"]["two@example.com"]["status"] == "partial_success"
    assert response["results"]["two@example.com"]["targets"]["sub2api"]["ok"] is False
