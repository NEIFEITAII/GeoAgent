"""LLM 流式工具调用解析回归测试（不依赖真实 API）。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, AsyncIterator

import pytest

from geoagent.config import Settings
from geoagent.core.llm import LLMService


def _tc(index: int, id_: str | None, name: str, arguments: str) -> Any:
    return SimpleNamespace(
        index=index,
        id=id_,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _chunk(tool_calls: list[Any]) -> Any:
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=tool_calls))]
    )


async def _fake_stream() -> AsyncIterator[Any]:
    # 模拟兼容端点：每个分片都重复发送完整 function.name，且存在两次工具调用。
    yield _chunk([_tc(0, "c1", "run_sql", '{"sql": "SELECT 1"}')])
    yield _chunk([_tc(0, None, "run_sql", "")])
    yield _chunk([_tc(1, "c2", "summarize_by_type", "{}")])
    yield _chunk([_tc(1, None, "summarize_by_type", "")])


class _FakeCompletions:
    @staticmethod
    async def create(**kwargs: Any) -> AsyncIterator[Any]:
        return _fake_stream()


class _FakeClient:
    chat = SimpleNamespace(completions=_FakeCompletions())


@pytest.mark.asyncio
async def test_stream_chat_does_not_merge_or_duplicate_tool_call_names(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings = Settings()
    service = LLMService(settings)
    service._client = lambda profile: _FakeClient()  # type: ignore[method-assign]
    message = await service.stream_chat(
        model=settings.default_model,
        messages=[{"role": "user", "content": "hi"}],
    )
    assert [tc.name for tc in message.tool_calls] == ["run_sql", "summarize_by_type"]
    assert [tc.id for tc in message.tool_calls] == ["c1", "c2"]
    assert message.tool_calls[0].arguments == {"sql": "SELECT 1"}
