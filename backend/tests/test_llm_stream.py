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


def _content_chunk(content: str) -> Any:
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=None))]
    )


async def _single_big_chunk_stream() -> AsyncIterator[Any]:
    yield _content_chunk("本期土地变化以耕地转为建设用地为主，" * 12)


class _BigChunkCompletions:
    @staticmethod
    async def create(**kwargs: Any) -> AsyncIterator[Any]:
        return _single_big_chunk_stream()


class _BigChunkClient:
    chat = SimpleNamespace(completions=_BigChunkCompletions())


class _NonStreamingCompletions:
    """模拟不支持流式的兼容端点：stream=True 直接报错，非流式可用。"""

    @staticmethod
    async def create(**kwargs: Any) -> Any:
        if kwargs.get("stream"):
            raise RuntimeError("streaming is not supported by this endpoint")
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="结论：新增建设用地 12000.5 平方米，其中耕地转入占 45.2%。",
                        tool_calls=None,
                        reasoning_content=None,
                    )
                )
            ]
        )


class _NonStreamingClient:
    chat = SimpleNamespace(completions=_NonStreamingCompletions())


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


@pytest.mark.asyncio
async def test_stream_chat_splits_big_upstream_chunks_before_forwarding(monkeypatch):
    """上游一次推大段文本时，仍切成小段转发，保证前端逐字追加的观感。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings = Settings()
    service = LLMService(settings)
    service._client = lambda profile: _BigChunkClient()  # type: ignore[method-assign]
    tokens: list[str] = []

    async def collect(delta: str) -> None:
        tokens.append(delta)

    message = await service.stream_chat(
        model=settings.default_model,
        messages=[{"role": "user", "content": "统计本期变化"}],
        on_token=collect,
    )
    assert message.tool_calls == []
    assert "".join(tokens) == message.content
    assert len(tokens) > 10  # 大段文本已被拆开
    assert all(len(t) <= 12 for t in tokens)


@pytest.mark.asyncio
async def test_stream_chat_falls_back_to_non_streaming_and_forwards_text(monkeypatch):
    """接口不支持流式时回退非流式调用，正文仍以小段 token 转发，不丢失回复。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings = Settings()
    service = LLMService(settings)
    service._client = lambda profile: _NonStreamingClient()  # type: ignore[method-assign]
    tokens: list[str] = []

    async def collect(delta: str) -> None:
        tokens.append(delta)

    message = await service.stream_chat(
        model=settings.default_model,
        messages=[{"role": "user", "content": "统计新增建设用地"}],
        on_token=collect,
    )
    assert message.content.startswith("结论：新增建设用地")
    assert "".join(tokens) == message.content
    assert all(len(t) <= 12 for t in tokens)
