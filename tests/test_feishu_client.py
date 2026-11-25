"""飞书客户端：mock outbox、token 缓存与请求形状。"""

from types import SimpleNamespace

from app.feishu import client


def test_mock_send_writes_outbox():
    client.send_text("oc_unit_1", "hello")
    entries = client.read_outbox()
    assert entries[-1]["msg_type"] == "text"
    assert entries[-1]["content"]["text"] == "hello"
    assert entries[-1]["chat_id"] == "oc_unit_1"


def test_mock_card_writes_outbox():
    card = {"header": {"title": {"content": "P1 · 高风险信号"}}, "elements": []}
    client.send_card("oc_unit_1", card)
    entries = client.read_outbox()
    assert entries[-1]["msg_type"] == "interactive"
    assert entries[-1]["content"]["header"]["title"]["content"].startswith("P1")


def test_mock_download_returns_png():
    data = client.download_resource("om_unit_1", "img_1", "image")
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_tenant_token_is_cached(monkeypatch):
    calls: list[dict] = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 0, "tenant_access_token": "t-token", "expire": 7200}

    def fake_post(url, json=None, timeout=None):
        calls.append({"url": url, "json": json})
        return FakeResponse()

    client._token_cache.clear()
    monkeypatch.setattr(
        client,
        "settings",
        SimpleNamespace(
            feishu_mode="live",
            feishu_app_id="app-id",
            feishu_app_secret="app-secret",
            feishu_domain="https://open.feishu.cn",
        ),
    )
    monkeypatch.setattr(client.httpx, "post", fake_post)

    assert client.tenant_token() == "t-token"
    assert client.tenant_token() == "t-token"
    assert len(calls) == 1
    assert calls[0]["url"].endswith("/open-apis/auth/v3/tenant_access_token/internal")
    assert calls[0]["json"] == {"app_id": "app-id", "app_secret": "app-secret"}
    client._token_cache.clear()
