"""土地变化监测快报生成（v1）。"""

from .briefing import build_briefing_docx, compute_briefing_stats

__all__ = ["build_briefing_docx", "compute_briefing_stats"]
