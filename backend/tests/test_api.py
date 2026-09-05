from __future__ import annotations

from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from geoagent.server.app import create_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GEOAGENT_DATA_DIR", str(tmp_path))
    app = create_app()
    return TestClient(app)


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_models_include_switchable_models(client):
    resp = client.get("/api/models")
    assert resp.status_code == 200
    models = resp.json()["models"]
    ids = {m["id"] for m in models}
    assert {"qwen3.7-flash", "qwen3.7-plus", "qwen3.7-max-2026-06-08"} <= ids
    assert len(ids) == 3
    assert all("available" in m for m in models)


def test_conversation_crud_and_model_switch(client):
    created = client.post(
        "/api/conversations",
        json={"title": "测试会话", "model": "qwen3.7-flash"},
    )
    assert created.status_code == 200
    conv = created.json()
    cid = conv["id"]
    assert conv["model"] == "qwen3.7-flash"

    listed = client.get("/api/conversations")
    assert any(c["id"] == cid for c in listed.json()["conversations"])

    switched = client.put(f"/api/conversations/{cid}/model", json={"model": "qwen3.7-plus"})
    assert switched.status_code == 200
    assert switched.json()["model"] == "qwen3.7-plus"

    bad = client.put(f"/api/conversations/{cid}/model", json={"model": "does-not-exist"})
    assert bad.status_code == 400


def test_send_message_requires_api_key(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    created = client.post(
        "/api/conversations",
        json={"model": "qwen3.7-flash"},
    )
    cid = created.json()["id"]
    resp = client.post(f"/api/conversations/{cid}/messages", json={"content": "你好"})
    assert resp.status_code == 502
    assert "OPENAI_API_KEY" in resp.json()["detail"]


def test_send_message_unknown_conversation(client):
    resp = client.post("/api/unknown/messages", json={"content": "hi"})
    assert resp.status_code == 404


def test_download_report_endpoint(tmp_path, client):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    sample = report_dir / "测试快报.docx"
    sample.write_bytes(b"PK\x03\x04fake-docx")
    resp = client.get(f"/api/files/reports/{quote(sample.name)}")
    assert resp.status_code == 404  # 默认 reports_dir 不是 tmp_path，应 404


def test_download_report_endpoint_with_env_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("GEOAGENT_DATA_DIR", str(tmp_path))
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    sample = report_dir / "测试快报.docx"
    sample.write_bytes(b"PK\x03\x04fake-docx")
    monkeypatch.setenv("GEOAGENT_REPORTS_DIR", str(report_dir))
    app = create_app()
    resp = TestClient(app).get(f"/api/files/reports/{quote(sample.name)}")
    assert resp.status_code == 200
    assert resp.content == b"PK\x03\x04fake-docx"
    bad = TestClient(app).get(f"/api/files/reports/{quote('..\\..\\secret.docx')}")
    assert bad.status_code == 400


def test_rollback_last_user_turn(client):
    """撤回最后一条用户消息时，其后的旧回答应一并被丢弃。"""
    created = client.post("/api/conversations", json={"model": "qwen3.7-flash"})
    cid = created.json()["id"]

    # 没有用户消息时返回 409
    empty = client.post(f"/api/conversations/{cid}/rollback-last-user-turn")
    assert empty.status_code == 409

    # 直接向 store 写入一轮“用户问题 + 助手回答”
    store = client.app.state.store
    store.add_message(cid, {"role": "user", "content": "旧问题"})
    store.add_message(cid, {"role": "assistant", "content": "旧回答"})

    resp = client.post(f"/api/conversations/{cid}/rollback-last-user-turn")
    assert resp.status_code == 200
    assert resp.json()["removed"] == "旧问题"

    messages = client.get(f"/api/conversations/{cid}/messages").json()["messages"]
    assert messages == []
