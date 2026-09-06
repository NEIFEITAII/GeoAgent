"""make_chart 统计图表工具测试（纯函数，不依赖网络/数据库）。"""

from __future__ import annotations

import asyncio

import pytest

from geoagent.agents.sql import SQLAgent
from geoagent.tools.chart import ChartSeries, make_chart
from geoagent.tools.registry import get_tools


def test_pie_chart_artifact():
    result = make_chart(
        chart_type="pie",
        title="建设用地图斑类型构成（平方米）",
        labels=["农用地", "建设用地", "未利用地"],
        series=[ChartSeries(name="面积", values=[1500.5, 800, 100])],
    )
    assert result.is_error is False
    assert len(result.artifacts) == 1
    chart = result.artifacts[0]
    assert chart.kind == "chart"
    assert chart.data["chart_type"] == "pie"
    assert chart.data["labels"] == ["农用地", "建设用地", "未利用地"]
    assert chart.data["series"][0]["values"] == [1500.5, 800, 100]


def test_bar_and_line_allow_multiple_series():
    series = [
        ChartSeries(name="耕地面积", values=[100, 90, 80]),
        ChartSeries(name="建设用地面积", values=[20, 30, 50]),
    ]
    for chart_type in ("bar", "line"):
        result = make_chart(
            chart_type=chart_type,
            title=f"历年{chart_type}对比（平方米）",
            labels=["2023年", "2024年", "2025年"],
            series=series,
        )
        assert result.artifacts[0].data["series"] == [s.model_dump() for s in series]


def test_tool_run_validates_llm_style_arguments():
    tool = get_tools("make_chart")[0]
    assert tool.name == "make_chart"
    assert tool.parameters["required"]  # Pydantic schema 已生成必填字段
    # 模拟 LLM 工具调用：series 以 dict 形式经 Pydantic 校验后执行。
    result = asyncio.run(
        tool.run(
            chart_type="bar",
            title="各区县新增建设用地面积",
            labels=["余杭区", "义乌市", "萧山区"],
            series=[{"name": "面积（平方米）", "values": [100, 90, 80]}],
        )
    )
    assert result.is_error is False
    assert result.artifacts[0].kind == "chart"
    assert result.artifacts[0].data["series"][0]["values"] == [100, 90, 80]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {
                "chart_type": "pie",
                "title": "多系列饼图",
                "labels": ["a", "b"],
                "series": [
                    {"name": "s1", "values": [1, 2]},
                    {"name": "s2", "values": [1, 2]},
                ],
            },
            "饼图只支持",
        ),
        (
            {
                "chart_type": "bar",
                "title": "长度不一致",
                "labels": ["a", "b"],
                "series": [{"name": "s1", "values": [1]}],
            },
            "不一致",
        ),
        (
            {
                "chart_type": "pie",
                "title": "负值饼图",
                "labels": ["a", "b"],
                "series": [{"name": "s1", "values": [1, -2]}],
            },
            "非负",
        ),
        (
            {
                "chart_type": "bar",
                "title": "单分类柱状图",
                "labels": ["a"],
                "series": [{"name": "s1", "values": [1]}],
            },
            "至少需要 2 个",
        ),
    ],
)
def test_make_chart_rejects_invalid_inputs(kwargs, message):
    with pytest.raises(ValueError, match=message):
        make_chart(**kwargs)


def test_sql_agent_includes_make_chart():
    names = {t.name for t in SQLAgent().tools}
    assert "make_chart" in names
