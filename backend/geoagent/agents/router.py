from __future__ import annotations

from typing import Any, Optional

from ..core.events import Event
from ..core.node import Node

ROUTE_TOOL = {
    "type": "function",
    "function": {
        "name": "route",
        "description": "Route the user request to the right agent",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "enum": ["sql", "geo", "elder_care", "chat"],
                    "description": (
                        "sql: database queries / land change statistics / tables / "
                        "aggregations / land-use type changes; "
                        "geo: spatial analysis / data / map / coordinates / buffer / "
                        "distance / area / layers; "
                        "elder_care: elderly care facility accessibility / nursing "
                        "homes / coverage / beds / supply-demand matching; "
                        "chat: anything else"
                    ),
                }
            },
            "required": ["target"],
        },
    },
}

ROUTER_SYSTEM_PROMPT = (
    "You are the intent router of GeoAgent. Decide which agent should handle the "
    "user request and set the target accordingly:\n"
    "- sql: questions that should be answered by querying the land-change database, "
    "such as table queries, statistics, aggregations, land-use type changes "
    "(耕地/建设用地/地类/图斑 etc.), or SQL questions.\n"
    "- geo: general spatial analysis, loading datasets, maps, coordinates, buffer, "
    "distance, area, layers, or map visualization.\n"
    "- elder_care: elderly care facility accessibility, nursing homes, coverage, "
    "beds, or supply-demand matching.\n"
    "- chat: anything else."
)

# 当路由模型不可用时的兜底方案。
ROUTE_GEO_KEYWORDS = (
    "缓冲区",
    "buffer",
    "距离",
    "面积",
    "图层",
    "数据",
    "坐标",
    "加载",
    "叠加",
    "裁剪",
    "相交",
    "地图",
    "poi",
    "兴趣点",
    "geojson",
    "shp",
    "矢量",
    "栅格",
    "分析",
)

ROUTE_ELDER_CARE_KEYWORDS = (
    "养老",
    "老年",
    "机构",
    "护理",
    "可达",
    "覆盖",
    "床位",
    "供需",
    "nursing",
    "elder",
    "care",
    "accessibility",
    "facility",
)

ROUTE_SQL_KEYWORDS = (
    "查询",
    "统计",
    "汇总",
    "sql",
    "数据库",
    "数据表",
    "表结构",
    "表字段",
    "地类",
    "图斑",
    "耕地",
    "建设用地",
    "变化图斑",
    "地类变化",
    "流向",
    "tblx",
    "dict",
    "select",
    "count",
    "sum",
    "group by",
    "join",
    "where",
)


class RouterNode(Node):
    """将用户请求路由到 SQL / 地理 / 养老 / 通用对话智能体。"""

    def __init__(self, model: Optional[str] = None) -> None:
        super().__init__(name="router")
        self.model = model

    async def exec(self, ctx: Any, payload: Any) -> tuple[str, Any]:
        user_text = str(payload)
        model = self.model or ctx.model
        target = "chat"
        reason = ""
        try:
            message = await ctx.llm.chat(
                model=model,
                messages=[
                    {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_text},
                ],
                tools=[ROUTE_TOOL],
            )
            if message.tool_calls:
                candidate = message.tool_calls[0].arguments.get("target")
            if candidate in ("sql", "geo", "elder_care", "chat"):
                target = candidate
            reason = message.content or ""
        except Exception:
            # 兜底：路由模型不可用时改用关键字启发式路由。
            lowered = user_text.lower()
            if any(k in lowered for k in ROUTE_ELDER_CARE_KEYWORDS):
                target = "elder_care"
            elif any(k in lowered for k in ROUTE_SQL_KEYWORDS):
                target = "sql"
            elif any(k in lowered for k in ROUTE_GEO_KEYWORDS):
                target = "geo"
            else:
                target = "chat"
            reason = "heuristic fallback (router model unavailable)"

        await ctx.emit(Event("route", {"target": target, "reason": reason}))
        # 记录本次路由结果，供 Agent 持久化到最终助手消息。
        ctx.route = target
        return target, payload
