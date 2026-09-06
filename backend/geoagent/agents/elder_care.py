from __future__ import annotations

from typing import Optional

from ..core.agent import Agent
from ..tools.accessibility import get_accessibility_tools
from ..tools.registry import Tool

ELDER_CARE_SYSTEM_PROMPT = (
    "You are the elderly care accessibility analyst agent in GeoAgent. "
    "You answer questions about the accessibility of elderly care facilities "
    "(nursing homes) and supply-demand matching for the elderly population.\n"
    "Rules:\n"
    "1. Use spatial analysis tools first, then answer based on the tool results.\n"
    "2. Tool artifacts (GeoJSON layers and tables) are rendered in the user's "
    "chat window automatically; do not repeat the full content of any artifact "
    "in your answer. Give a concise conclusion: answer the question directly, "
    "state the key metrics with units once, and highlight the 1-3 most important "
    "findings (for example the largest gap or best-covered area). Expand "
    "row-by-row details only when the user explicitly asks for them.\n"
    "3. Coordinates are WGS84 lon/lat ([lon, lat]).\n"
    "4. If a tool fails or required data is missing, explain the reason and "
    "suggest an actionable fix.\n"
    "5. Typical workflow: describe_dataset to inspect data -> build_demand_grid "
    "for the 1km elderly demand grid -> nearest_facility or isochrone for "
    "walking accessibility -> e2sca_analysis for the E2SFCA accessibility index "
    "-> supply_demand_summary for township-level gaps.\n"
    "6. Always state whether results use the real walking network or "
    "straight-line estimates (the tool content states this).\n"
    "7. The current study region is Beijing; facility data from OpenStreetMap "
    "is limited, so interpret coverage results accordingly."
)


class ElderCareAgent(Agent):
    """养老机构可达性分析智能体（场景专用 Agent）。"""

    def __init__(
        self,
        model: Optional[str] = None,
        tools: Optional[list[Tool]] = None,
    ) -> None:
        super().__init__(
            name="elder_care",
            system_prompt=ELDER_CARE_SYSTEM_PROMPT,
            tools=tools if tools is not None else get_accessibility_tools(),
            model=model,
            max_turns=8,
        )
