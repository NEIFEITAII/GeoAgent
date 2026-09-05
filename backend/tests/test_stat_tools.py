"""确定性统计工具的参数校验测试（不依赖真实库）。"""

from __future__ import annotations

import pytest

from geoagent.core.llm import ToolCall
from geoagent.tools import ToolExecutor
from geoagent.tools.stat import get_stat_tools


class _Ctx:
    pg = None


@pytest.mark.asyncio
async def test_fragment_stats_rejects_non_positive_threshold():
    executor = ToolExecutor(get_stat_tools())
    result = await executor.execute(
        ToolCall(id="c1", name="fragment_stats", arguments={"threshold_m2": -1}),
        ctx=_Ctx(),
    )
    assert result.is_error
    assert "参数校验失败" in result.content


@pytest.mark.asyncio
async def test_top_n_params_are_bounded():
    executor = ToolExecutor(get_stat_tools())
    for name, args in (
        ("top_conversions", {"top_n": 0}),
        ("construction_change_summary", {"top_n": 101}),
    ):
        result = await executor.execute(ToolCall(id="c2", name=name, arguments=args), ctx=_Ctx())
        assert result.is_error
        assert "参数校验失败" in result.content


def test_stat_tool_registry_names():
    names = {t.name for t in get_stat_tools()}
    assert {
        "summarize_by_type",
        "fragment_stats",
        "farmland_flow_summary",
        "construction_change_summary",
        "top_conversions",
    } <= names
