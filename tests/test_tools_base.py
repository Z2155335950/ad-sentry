"""工具层运行时：预算、熔断、缓存与 ToolCall 记账。"""

from app.db import engine, init_db
from app.models import ToolCall
from app.tools import base
from sqlmodel import Session, select

init_db()


def _runtime(budget: float = 0.25) -> base.ToolRuntime:
    session = Session(engine)
    return base.ToolRuntime(session, job_id=None, budget_usd=budget)


def test_budget_exhausted_skips_without_calling():
    runtime = _runtime(budget=0.0)
    called = []
    result = runtime.call(
        tool="budget-test",
        input_hash="h1",
        fn=lambda: called.append(1) or {"x": 1},
        cost_usd=0.01,
        mode="live",
    )
    assert result.status == "skipped"
    assert result.error == "budget_exhausted"
    assert called == []


def test_circuit_breaker_opens_after_three_failures():
    runtime = _runtime()
    tool = "breaker-test"

    def boom():
        raise RuntimeError("boom")

    for _ in range(3):
        result = runtime.call(tool=tool, input_hash="h", fn=boom, mode="mock")
        assert result.status == "error"
    opened = runtime.call(tool=tool, input_hash="h", fn=boom, mode="mock")
    assert opened.status == "skipped"
    assert opened.error == "circuit_open"


def test_live_cache_hit_avoids_second_call():
    runtime = _runtime()
    calls = []

    def produce():
        calls.append(1)
        return {"matches": [{"url": "https://example.com/a"}]}

    first = runtime.call(
        tool="cache-test", input_hash="same-input", fn=produce, cost_usd=0.0, mode="live"
    )
    second = runtime.call(
        tool="cache-test", input_hash="same-input", fn=produce, cost_usd=0.0, mode="live"
    )
    assert first.status == "ok"
    assert second.status == "cached"
    assert len(calls) == 1


def test_tool_calls_are_recorded():
    runtime = _runtime()
    runtime.call(tool="record-test", input_hash="abc", fn=lambda: {"ok": 1}, mode="mock")
    with Session(engine) as session:
        rows = session.exec(select(ToolCall).where(ToolCall.tool == "record-test")).all()
    assert rows
    assert rows[0].input_hash == "abc"
    assert rows[0].ok is True


def test_auto_mode_uses_live_only_when_configured(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "tools_mode", "auto")
    monkeypatch.setattr(settings, "google_vision_api_key", "")
    monkeypatch.setattr(settings, "google_application_credentials", "")
    assert base.resolve_mode("image_search") == "mock"

    monkeypatch.setattr(settings, "google_vision_api_key", "test-key")
    assert base.resolve_mode("image_search") == "live"


def test_demo_cache_takes_priority_over_live(monkeypatch):
    import hashlib
    from pathlib import Path

    from app.config import settings

    root = Path(__file__).resolve().parents[1]
    sha256 = hashlib.sha256(
        (root / "fixtures" / "media" / "risk_ad_10s.mp4").read_bytes()
    ).hexdigest()

    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "tools_mode", "live")
    assert base.effective_mode("image_search", sha256, 0) == "demo"
    assert base.demo_entry("image_search", sha256, 0) is not None
    # 缓存缺失的帧才回落真实调用
    assert base.effective_mode("image_search", sha256, 99) == "live"

    monkeypatch.setattr(settings, "demo_mode", False)
    assert base.effective_mode("image_search", sha256, 0) == "live"
