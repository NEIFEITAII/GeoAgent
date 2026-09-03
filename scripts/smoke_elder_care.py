"""本地冒烟：用真实北京数据跑通养老可达性分析链路（不调用 LLM）。

用法：
    backend/.venv/Scripts/python.exe scripts/smoke_elder_care.py

覆盖：需求网格 → 最近设施 → 等时圈 → E2SFCA → 街道供需匹配。
"""

from __future__ import annotations

import sys
import time

from geoagent.tools.accessibility import (
    build_demand_grid_tool,
    e2sca_analysis,
    isochrone,
    nearest_facility,
    supply_demand_summary,
)


def _report(name: str, result: Any, t0: float) -> None:
    elapsed = round(time.time() - t0, 1)
    kinds = [(a.kind, a.name) for a in result.artifacts]
    print(f"\n[{name}] {elapsed}s | error={result.is_error}")
    print("  content:", result.content[:200])
    print("  artifacts:", kinds)
    for artifact in result.artifacts:
        if artifact.kind == "geojson":
            print(f"    {artifact.name}: {len(artifact.data['features'])} 要素")


def main() -> int:
    """执行冒烟流程。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    t0 = time.time()
    grid = build_demand_grid_tool()
    _report("build_demand_grid", grid, t0)

    t0 = time.time()
    nearest = nearest_facility()
    _report("nearest_facility", nearest, t0)

    t0 = time.time()
    iso = isochrone()
    _report("isochrone", iso, t0)

    t0 = time.time()
    e2sca = e2sca_analysis()
    _report("e2sca_analysis", e2sca, t0)

    t0 = time.time()
    supply = supply_demand_summary()
    _report("supply_demand_summary", supply, t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
