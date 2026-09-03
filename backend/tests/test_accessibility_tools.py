"""可达性工具测试（仅演示数据与参数校验，不依赖真实数据 / 网络）。"""

from __future__ import annotations

import pytest

from geoagent.core.llm import ToolCall
from geoagent.tools import ToolExecutor, get_tools


@pytest.mark.asyncio
async def test_describe_dataset_demo():
    executor = ToolExecutor(get_tools("describe_dataset"))
    result = await executor.execute(
        ToolCall(
            id="c1",
            name="describe_dataset",
            arguments={"dataset_id": "beijing_pois"},
        ),
        ctx=None,
    )
    assert not result.is_error
    assert result.artifacts[0].kind == "table"
    assert "6" in result.content


@pytest.mark.asyncio
async def test_describe_dataset_missing():
    executor = ToolExecutor(get_tools("describe_dataset"))
    result = await executor.execute(
        ToolCall(
            id="c2",
            name="describe_dataset",
            arguments={"dataset_id": "nope"},
        ),
        ctx=None,
    )
    assert result.is_error


@pytest.mark.asyncio
async def test_unknown_region_error():
    executor = ToolExecutor(get_tools("build_demand_grid"))
    result = await executor.execute(
        ToolCall(
            id="c3",
            name="build_demand_grid",
            arguments={"region": "shanghai"},
        ),
        ctx=None,
    )
    assert result.is_error
    assert "shanghai" in result.content


def test_accessibility_tool_schema():
    tool = get_tools("e2sca_analysis")[0]
    schema = tool.to_llm_format()
    props = schema["function"]["parameters"]["properties"]
    assert "thresholds" in props
    assert "decay" in props
    assert props["decay"]["enum"] == ["gaussian", "step"]
