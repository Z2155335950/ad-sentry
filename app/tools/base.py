"""工具层公共设施：统一结果、预算、熔断、缓存与调用记账（PROMPT §2/§7/§14）。

业务代码只通过适配器调用外部服务；每个适配器都先写 mock，再决定是否接真。
任何单工具失败只丢该信号，不抛到主流程。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from pathlib import Path

from sqlmodel import Session

from app.config import settings
from app.models import ToolCache, ToolCall

logger = logging.getLogger(__name__)

_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="adsentry-tool")

FAILURE_WINDOW_SECONDS = 60.0
FAILURE_THRESHOLD = 3
OPEN_SECONDS = 60.0


class ToolUnavailable(RuntimeError):
    """未配置凭证或依赖缺失；降级而不是抛到主流程。"""


class ToolError(RuntimeError):
    """工具调用失败（超时、HTTP 错误、响应不可解析）。"""


@dataclass
class ToolResult:
    tool: str
    status: str  # ok | empty | unavailable | error | skipped | cached
    data: dict = field(default_factory=dict)
    mode: str = "mock"
    latency_ms: int = 0
    cost_usd: float = 0.0
    error: str | None = None

    @property
    def usable(self) -> bool:
        return self.status in {"ok", "empty", "cached"}


class _Breaker:
    def __init__(self) -> None:
        self.failures: deque[float] = deque()
        self.open_until = 0.0
        self.lock = threading.Lock()

    def allow(self) -> bool:
        now = time.monotonic()
        with self.lock:
            if self.open_until > now:
                return False
            if self.open_until:
                self.open_until = 0.0
                self.failures.clear()
            while self.failures and now - self.failures[0] > FAILURE_WINDOW_SECONDS:
                self.failures.popleft()
            return True

    def fail(self) -> None:
        now = time.monotonic()
        with self.lock:
            self.failures.append(now)
            while self.failures and now - self.failures[0] > FAILURE_WINDOW_SECONDS:
                self.failures.popleft()
            if len(self.failures) >= FAILURE_THRESHOLD:
                self.open_until = now + OPEN_SECONDS

    def success(self) -> None:
        with self.lock:
            self.failures.clear()
            self.open_until = 0.0


_BREAKERS: dict[str, _Breaker] = {}
_BREAKERS_LOCK = threading.Lock()


def breaker(tool: str) -> _Breaker:
    with _BREAKERS_LOCK:
        if tool not in _BREAKERS:
            _BREAKERS[tool] = _Breaker()
        return _BREAKERS[tool]


@dataclass
class ToolBudget:
    limit_usd: float
    spent_usd: float = 0.0

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.limit_usd - self.spent_usd)

    def spend(self, cost_usd: float) -> None:
        self.spent_usd += max(0.0, cost_usd)


def resolve_mode(tool: str) -> str:
    mode = (settings.tools_mode or "mock").lower()
    if mode == "auto":
        return "live" if _configured(tool) else "mock"
    return mode if mode in {"mock", "live", "off"} else "mock"


def _configured(tool: str) -> bool:
    if tool in ("image_search", "logo"):
        return bool(settings.google_vision_api_key or settings.google_application_credentials)
    if tool == "music":
        return bool(
            settings.acrcloud_host
            and settings.acrcloud_access_key
            and settings.acrcloud_access_secret
        )
    if tool == "celebrity":
        return bool(settings.aws_access_key_id and settings.aws_secret_access_key)
    return False


def load_mock(tool: str) -> dict:
    path = Path(settings.fixtures_dir) / "cache" / f"{tool}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("mock fixture 读取失败 %s: %s", path, exc)
        return {}


def demo_cache_path(tool: str, asset_sha256: str) -> Path:
    """DEMO_MODE 的演示缓存：fixtures/cache/<tool>/<asset_sha256>.json（PROMPT §16）。"""
    return Path(settings.fixtures_dir) / "cache" / tool / f"{asset_sha256}.json"


def demo_entry(tool: str, asset_sha256: str, index: int | None = None) -> dict | None:
    path = demo_cache_path(tool, asset_sha256)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("演示缓存读取失败 %s: %s", path, exc)
        return None
    if index is None:
        return data or None
    section = data.get("scenes") or data.get("windows") or {}
    return section.get(str(index))


def effective_mode(tool: str, asset_sha256: str, index: int | None = None) -> str:
    """演示缓存优先；缺失时才回落到 mock / live（PROMPT §16）。"""
    if settings.demo_mode and demo_entry(tool, asset_sha256, index) is not None:
        return "demo"
    return resolve_mode(tool)


def mock_entry(tool: str, asset_sha256: str, scene_index: int | None = None) -> dict | None:
    data = load_mock(tool).get("assets", {}).get(asset_sha256) or {}
    if scene_index is None:
        return data or None
    scenes = data.get("scenes") or {}
    return scenes.get(str(scene_index))


def _cache_key(tool: str, mode: str, input_hash: str) -> str:
    return f"{tool}:{mode}:{input_hash}"


def file_sha256(path: str | Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cache_get(session: Session, key: str) -> dict | None:
    row = session.get(ToolCache, key)
    return row.result_json if row is not None else None


def _cache_put(session: Session, key: str, tool: str, data: dict) -> None:
    row = session.get(ToolCache, key)
    if row is None:
        session.add(ToolCache(key=key, tool=tool, result_json=data))
    else:
        row.result_json = data
        session.add(row)
    session.commit()


class ToolRuntime:
    """一次 Job 内的工具调用上下文：预算、缓存、熔断、ToolCall 记账。"""

    def __init__(
        self,
        session: Session,
        job_id: int | None = None,
        budget_usd: float | None = None,
    ) -> None:
        self.session = session
        self.job_id = job_id
        self.budget = ToolBudget(
            settings.budget_per_asset_usd if budget_usd is None else budget_usd
        )

    def call(
        self,
        *,
        tool: str,
        input_hash: str,
        fn,
        cost_usd: float = 0.0,
        mode: str | None = None,
        timeout: float | None = None,
    ) -> ToolResult:
        mode = mode or resolve_mode(tool)
        if mode == "off":
            return ToolResult(tool=tool, status="skipped", mode=mode, error="tools_off")
        if not breaker(tool).allow():
            self._record(tool, input_hash, ok=False, cost=0.0, error="circuit_open")
            return ToolResult(tool=tool, status="skipped", mode=mode, error="circuit_open")
        if self.budget.remaining_usd < cost_usd:
            self._record(tool, input_hash, ok=False, cost=0.0, error="budget_exhausted")
            return ToolResult(
                tool=tool, status="skipped", mode=mode, error="budget_exhausted"
            )

        key = _cache_key(tool, mode, input_hash)
        if mode == "live":
            cached = _cache_get(self.session, key)
            if cached is not None:
                self._record(tool, input_hash, ok=True, cost=0.0, error=None, cached=True)
                return ToolResult(tool=tool, status="cached", data=cached, mode=mode)

        started = time.perf_counter()
        try:
            data = _run_with_timeout(fn, timeout)
        except ToolUnavailable as exc:
            self._record(tool, input_hash, ok=False, cost=0.0, error=str(exc)[:300])
            return ToolResult(
                tool=tool, status="unavailable", mode=mode, error=str(exc)[:300]
            )
        except Exception as exc:  # 单工具失败只丢该信号
            logger.warning("工具 %s 失败: %s", tool, exc)
            breaker(tool).fail()
            self._record(tool, input_hash, ok=False, cost=0.0, error=str(exc)[:300])
            return ToolResult(tool=tool, status="error", mode=mode, error=str(exc)[:300])

        latency_ms = int((time.perf_counter() - started) * 1000)
        cost = 0.0 if mode in {"mock", "demo"} else cost_usd
        self.budget.spend(cost)
        breaker(tool).success()
        status = "empty" if not data else "ok"
        self._record(tool, input_hash, ok=True, cost=cost, latency_ms=latency_ms)
        if mode == "live" and data:
            _cache_put(self.session, key, tool, data)
        return ToolResult(
            tool=tool,
            status=status,
            data=data or {},
            mode=mode,
            latency_ms=latency_ms,
            cost_usd=cost,
        )

    def _record(
        self,
        tool: str,
        input_hash: str,
        *,
        ok: bool,
        cost: float,
        error: str | None = None,
        latency_ms: int | None = None,
        cached: bool = False,
    ) -> None:
        note = error
        if cached:
            note = "cache_hit"
        self.session.add(
            ToolCall(
                job_id=self.job_id,
                tool=tool,
                input_hash=input_hash,
                ok=ok,
                latency_ms=latency_ms,
                cost_usd=cost,
                error=note,
            )
        )
        self.session.commit()


def _run_with_timeout(fn, timeout: float | None):
    if not timeout:
        return fn()
    future = _EXECUTOR.submit(fn)
    try:
        return future.result(timeout=timeout)
    except FutureTimeout as exc:
        raise ToolError(f"超时 {timeout}s") from exc
