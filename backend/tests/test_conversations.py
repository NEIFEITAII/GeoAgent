"""会话管理测试：删除会话与首条消息自动标题。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from geoagent.server.app import create_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GEOAGENT_DATA_DIR", str(tmp_path))
    app = create_app()
    return TestClient(app)


def test_delete_conversation(client):
    created = client.post("/api/conversations", json={"title": "临时"}).json()
    cid = created["id"]
    assert client.get(f"/api/conversations/{cid}").status_code == 200
    resp = client.delete(f"/api/conversations/{cid}")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert client.get(f"/api/conversations/{cid}").status_code == 404
    listed = client.get("/api/conversations").json()["conversations"]
    assert all(c["id"] != cid for c in listed)


def test_delete_missing_returns_404(client):
    resp = client.delete("/api/conversations/does-not-exist")
    assert resp.status_code == 404


def test_auto_title_from_first_message(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cid = client.post("/api/conversations", json={"title": "新会话"}).json()["id"]
    content = "北京哪个区的养老床位缺口最大"
    # 未配置 key 时发消息返回 502，但标题应已由首条消息生成。
    resp = client.post(f"/api/conversations/{cid}/messages", json={"content": content})
    assert resp.status_code == 502
    conv = client.get(f"/api/conversations/{cid}").json()
    assert conv["title"] == content


def test_auto_title_truncates_long_message(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cid = client.post("/api/conversations", json={"title": "新会话 12:00"}).json()["id"]
    long_text = "请帮我分析一下北京市朝阳区、海淀区、丰台区养老机构的可达性和供需缺口情况"
    client.post(f"/api/conversations/{cid}/messages", json={"content": long_text})
    conv = client.get(f"/api/conversations/{cid}").json()
    assert conv["title"].endswith("…")
    assert len(conv["title"]) <= 21


def test_custom_title_not_overwritten(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cid = client.post("/api/conversations", json={"title": "我的养老调研"}).json()["id"]
    client.post(
        f"/api/conversations/{cid}/messages", json={"content": "随便一个问题"}
    )
    conv = client.get(f"/api/conversations/{cid}").json()
    assert conv["title"] == "我的养老调研"


def test_conversations_list_newest_first(client):
    id_a = client.post("/api/conversations", json={"title": "A"}).json()["id"]
    id_b = client.post("/api/conversations", json={"title": "B"}).json()["id"]
    listed = client.get("/api/conversations").json()["conversations"]
    ids = [c["id"] for c in listed]
    assert ids.index(id_b) < ids.index(id_a)
