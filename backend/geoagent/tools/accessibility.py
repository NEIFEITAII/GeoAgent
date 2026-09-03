"""养老可达性分析工具（供 ElderCareAgent 使用）。

工具覆盖：数据描述、1km 网格需求、最近设施、等时圈、
E2SFCA 可达性指数、按街道/区的供需匹配。
路网数据存在时使用真实步行路网，否则回退到直线距离估计并在结果中标注。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from ..analysis.datasets import default_registry
from ..analysis.e2sca import (
    DemandPoint,
    Facility,
    aggregate_accessibility_by_polygons,
    attach_accessibility_to_grid,
    e2sca_accessibility,
    supply_demand_by_polygons,
)
from ..analysis.grid import (
    aggregate_by_polygons,
    build_demand_grid,
    grid_centroids,
    load_census,
)
from ..analysis.network import (
    euclidean_travel_min,
    get_walking_network,
)
from .registry import get_tools, register_tool
from .result import Artifact, ToolResult


class DescribeDatasetParams(BaseModel):
    dataset_id: str = Field(description="Dataset id from list_datasets")


class BuildDemandGridParams(BaseModel):
    region: str = Field(default="beijing", description="Study region (only 'beijing' supported now)")
    cell_size_km: float = Field(default=1.0, gt=0.1, le=10, description="Grid cell size in km")


class NearestFacilityParams(BaseModel):
    facility_dataset: str = Field(default="beijing_nursing_homes", description="Facility point dataset id")
    region: str = Field(default="beijing", description="Study region")
    walking_speed_kmh: float = Field(default=4.2, gt=0.5, le=10, description="Walking speed km/h")
    max_walk_min: float = Field(default=60.0, gt=0, le=180, description="Maximum walking time in minutes")


class IsochroneParams(BaseModel):
    facility_dataset: str = Field(default="beijing_nursing_homes", description="Facility point dataset id")
    facility_index: int = Field(default=0, ge=0, description="Zero-based index of the facility in the dataset")
    thresholds: list[float] = Field(default=[15, 30], min_length=1, description="Walking time thresholds in minutes")
    walking_speed_kmh: float = Field(default=4.2, gt=0.5, le=10, description="Walking speed km/h")


class E2scaParams(BaseModel):
    facility_dataset: str = Field(default="beijing_nursing_homes", description="Facility point dataset id")
    region: str = Field(default="beijing", description="Study region")
    thresholds: list[float] = Field(default=[15, 30], min_length=1, description="Catchment thresholds in minutes")
    decay: Literal["gaussian", "step"] = Field(default="gaussian", description="Distance decay: gaussian or step")
    capacity_field: str = Field(default="beds", description="Property name for facility capacity (beds)")
    default_capacity: float = Field(default=100.0, gt=0, description="Fallback capacity when the field is missing")
    walking_speed_kmh: float = Field(default=4.2, gt=0.5, le=10, description="Walking speed km/h")


class SupplyDemandParams(BaseModel):
    facility_dataset: str = Field(default="beijing_nursing_homes", description="Facility point dataset id")
    region: str = Field(default="beijing", description="Study region")
    catchment_min: float = Field(default=30.0, gt=0, le=180, description="Catchment walking time in minutes")
    walking_speed_kmh: float = Field(default=4.2, gt=0.5, le=10, description="Walking speed km/h")


def _check_region(region: str) -> None:
    if region != "beijing":
        raise ValueError(f"暂不支持区域: {region}（当前仅支持 beijing）")


@lru_cache(maxsize=4)
def _demand_grid_for_region(region: str, cell_size_km: float) -> dict[str, Any]:
    """构建并缓存指定区域的 1km 需求网格。"""
    _check_region(region)
    registry = default_registry()
    districts = registry.get("beijing_districts")
    if districts is None:
        raise ValueError("缺少区划数据集 beijing_districts，请先运行数据准备")
    census = load_census()
    if not census:
        raise ValueError("缺少人口数据（beijing_census_2020.csv）")
    return build_demand_grid(districts, census, cell_size_km)


def _load_facilities(
    dataset_id: str,
    capacity_field: str,
    default_capacity: float,
) -> list[Facility]:
    registry = default_registry()
    fc = registry.get(dataset_id)
    if fc is None:
        raise ValueError(f"数据集不存在: {dataset_id}")
    facilities: list[Facility] = []
    for feat in fc.get("features", []):
        geom = feat.get("geometry")
        if geom is None or geom.get("type") != "Point":
            continue
        lon, lat = geom["coordinates"]
        props = feat.get("properties", {})
        try:
            capacity = float(props.get(capacity_field, default_capacity))
        except (TypeError, ValueError):
            capacity = default_capacity
        facilities.append(
            Facility(
                id=f"f{len(facilities)}",
                lon=float(lon),
                lat=float(lat),
                capacity=capacity,
                name=str(props.get("name", "")),
            )
        )
    if not facilities:
        raise ValueError(f"数据集 {dataset_id} 中没有点要素")
    return facilities


def _walk_times_matrix(
    demand: list[DemandPoint],
    facilities: list[Facility],
    speed_kmh: float,
    max_min: float,
) -> tuple[dict[str, dict[str, float]], str]:
    """计算需求点 × 设施点步行时间矩阵。

    返回 (times, mode)，mode 为 "network"（真实路网）或 "euclidean"（直线估计）。
    """
    network = get_walking_network()
    times: dict[str, dict[str, float]] = {}
    if network.available:
        facility_nodes = [network.snap(f.lon, f.lat) for f in facilities]
        demand_nodes = [network.snap(d.lon, d.lat) for d in demand]
        reachable = network.travel_times(facility_nodes, max_min)
        for fi, facility in enumerate(facilities):
            node_times = reachable.get(facility_nodes[fi], {})
            times[facility.id] = {
                d.id: t
                for di, d in enumerate(demand)
                if (t := node_times.get(demand_nodes[di])) is not None
            }
        return times, "network"
    for facility in facilities:
        fmap: dict[str, float] = {}
        for d in demand:
            t = euclidean_travel_min(facility.lon, facility.lat, d.lon, d.lat, speed_kmh)
            if t <= max_min:
                fmap[d.id] = t
        times[facility.id] = fmap
    return times, "euclidean"


def _table_artifact(name: str, columns: list[str], rows: list[list[Any]]) -> Artifact:
    return Artifact(kind="table", name=name, data={"columns": columns, "rows": rows})


@register_tool(
    "describe_dataset",
    "Describe a geospatial dataset: feature count, geometry types, fields and bbox",
    DescribeDatasetParams,
)
def describe_dataset(dataset_id: str) -> ToolResult:
    info = default_registry().describe(dataset_id)
    if info is None:
        return ToolResult(
            tool_call_id="", name="describe_dataset",
            content=f"数据集不存在: {dataset_id}",
            is_error=True,
        )
    rows = [
        ["feature_count", str(info["feature_count"])],
        ["geometry_types", str(info["geometry_types"])],
        ["fields", ", ".join(info["fields"])],
        ["bbox", str(info["bbox"])],
    ]
    content = (
        f"数据集 {dataset_id}: {info['feature_count']} 个要素，"
        f"字段 {len(info['fields'])} 个，bbox={info['bbox']}"
    )
    return ToolResult(
        tool_call_id="", name="describe_dataset", content=content,
        artifacts=[_table_artifact("dataset_info", ["属性", "值"], rows)],
    )


@register_tool(
    "build_demand_grid",
    "Build a 1km demand grid with estimated elderly population over the study region",
    BuildDemandGridParams,
)
def build_demand_grid_tool(region: str = "beijing", cell_size_km: float = 1.0) -> ToolResult:
    grid = _demand_grid_for_region(region, cell_size_km)
    total_elderly = round(sum(f["properties"]["elderly_pop"] for f in grid["features"]), 2)
    content = (
        f"已生成 {region} 的 {cell_size_km}km 需求网格，共 {len(grid['features'])} 个网格，"
        f"估算老年人口合计 {total_elderly} 人（按七普区级比例面积分摊）。"
    )
    return ToolResult(
        tool_call_id="", name="build_demand_grid", content=content,
        artifacts=[Artifact(kind="geojson", name="demand_grid", data=grid)],
    )


@register_tool(
    "nearest_facility",
    "Compute walking time from every demand grid cell to the nearest elderly care facility",
    NearestFacilityParams,
)
def nearest_facility(
    facility_dataset: str = "beijing_nursing_homes",
    region: str = "beijing",
    walking_speed_kmh: float = 4.2,
    max_walk_min: float = 60.0,
) -> ToolResult:
    _check_region(region)
    grid = _demand_grid_for_region(region, 1.0)
    points = grid_centroids(grid)["features"]
    demand = [
        DemandPoint(
            id=str(p["properties"]["cell_id"]),
            lon=p["properties"]["lon"],
            lat=p["properties"]["lat"],
            elderly_pop=p["properties"]["elderly_pop"],
        )
        for p in points
    ]
    facilities = _load_facilities(facility_dataset, "beds", 100.0)
    times, mode = _walk_times_matrix(demand, facilities, walking_speed_kmh, max_walk_min)

    by_id = {f.id: f for f in facilities}
    out_features = []
    within_15 = within_30 = served = 0
    min_times: list[float] = []
    for p in points:
        pid = p["properties"]["cell_id"]
        best_t = float("inf")
        best_fid: Optional[str] = None
        for fid, fmap in times.items():
            t = fmap.get(str(pid))
            if t is not None and t < best_t:
                best_t = t
                best_fid = fid
        props = dict(p["properties"])
        props["nearest_min"] = round(best_t, 2) if best_fid else None
        props["nearest_facility"] = by_id[best_fid].name if best_fid else None
        out_features.append({"type": "Feature", "properties": props, "geometry": p["geometry"]})
        if best_fid:
            served += 1
            min_times.append(best_t)
            if best_t <= 15:
                within_15 += 1
            if best_t <= 30:
                within_30 += 1
    total = len(points)
    rows = [
        ["有可达设施的网格数", f"{served} / {total} ({served / total * 100:.1f}%)"],
        ["15 分钟内可达", f"{within_15} ({within_15 / total * 100:.1f}%)"],
        ["30 分钟内可达", f"{within_30} ({within_30 / total * 100:.1f}%)"],
        ["平均最短步行时间（分钟）", f"{sum(min_times) / len(min_times):.2f}" if min_times else "—"],
        ["时间口径", "真实步行路网" if mode == "network" else "直线距离估计"],
    ]
    content = (
        f"已计算 {served}/{total} 个需求网格到最近养老机构的步行时间"
        f"（{mode}），15 分钟内可达 {within_15} 个网格。"
    )
    return ToolResult(
        tool_call_id="", name="nearest_facility", content=content,
        artifacts=[
            Artifact(kind="geojson", name="nearest_facility", data={"type": "FeatureCollection", "features": out_features}),
            _table_artifact("coverage_summary", ["指标", "数值"], rows),
        ],
    )


@register_tool(
    "isochrone",
    "Compute walking isochrone polygons around an elderly care facility",
    IsochroneParams,
)
def isochrone(
    facility_dataset: str = "beijing_nursing_homes",
    facility_index: int = 0,
    thresholds: list[float] = [15, 30],
    walking_speed_kmh: float = 4.2,
) -> ToolResult:
    facilities = _load_facilities(facility_dataset, "beds", 100.0)
    if facility_index >= len(facilities):
        return ToolResult(
            tool_call_id="", name="isochrone",
            content=f"facility_index {facility_index} 超出范围（共 {len(facilities)} 个设施）",
            is_error=True,
        )
    facility = facilities[facility_index]
    network = get_walking_network()
    polys = network.isochrone_polygons(
        facility.lon, facility.lat, thresholds, walking_speed_kmh
    )
    features = [
        {
            "type": "Feature",
            "properties": {"threshold_min": p["threshold_min"], "area_km2": p["area_km2"]},
            "geometry": p["polygon"],
        }
        for p in polys
    ]
    rows = [[p["threshold_min"], p["area_km2"]] for p in polys]
    mode = "network" if network.available else "euclidean"
    area_desc = [f"{p['threshold_min']} 分钟约 {p['area_km2']} km²" for p in polys]
    content = (
        f"已生成 {facility.name or facility.id} 的步行等时圈（{mode}），"
        f"{'; '.join(area_desc)}。"
    )
    return ToolResult(
        tool_call_id="", name="isochrone", content=content,
        artifacts=[
            Artifact(kind="geojson", name="isochrones", data={"type": "FeatureCollection", "features": features}),
            _table_artifact("isochrone_areas", ["阈值（分钟）", "面积（km²）"], rows),
        ],
    )


@register_tool(
    "e2sca_analysis",
    "Run E2SFCA accessibility analysis: supply-demand ratio and accessibility index per grid cell and township",
    E2scaParams,
)
def e2sca_analysis(
    facility_dataset: str = "beijing_nursing_homes",
    region: str = "beijing",
    thresholds: list[float] = [15, 30],
    decay: str = "gaussian",
    capacity_field: str = "beds",
    default_capacity: float = 100.0,
    walking_speed_kmh: float = 4.2,
) -> ToolResult:
    _check_region(region)
    grid = _demand_grid_for_region(region, 1.0)
    points = grid_centroids(grid)["features"]
    demand = [
        DemandPoint(
            id=str(p["properties"]["cell_id"]),
            lon=p["properties"]["lon"],
            lat=p["properties"]["lat"],
            elderly_pop=p["properties"]["elderly_pop"],
        )
        for p in points
    ]
    facilities = _load_facilities(facility_dataset, capacity_field, default_capacity)
    times, mode = _walk_times_matrix(demand, facilities, walking_speed_kmh, max(thresholds))
    results = e2sca_accessibility(demand, facilities, times, thresholds, decay)
    grid = attach_accessibility_to_grid(grid, results)

    registry = default_registry()
    townships = registry.get("beijing_townships")
    district_agg: Optional[dict[str, Any]] = None
    township_agg: Optional[dict[str, Any]] = None
    if townships is not None:
        township_agg = aggregate_accessibility_by_polygons(grid, townships, "name")
    districts = registry.get("beijing_districts")
    if districts is not None:
        district_agg = aggregate_accessibility_by_polygons(grid, districts, "name")

    artifacts: list[Artifact] = [
        Artifact(kind="geojson", name="e2sca_grid", data=grid),
    ]
    if township_agg is not None:
        artifacts.append(Artifact(kind="geojson", name="e2sca_townships", data=township_agg))
    if district_agg is not None:
        artifacts.append(Artifact(kind="geojson", name="e2sca_districts", data=district_agg))

    ranked = sorted(
        township_agg["features"], key=lambda f: f["properties"].get("accessibility_weighted", 0), reverse=True
    ) if township_agg else []
    rows = [
        [f["properties"].get("name"), f["properties"].get("accessibility_weighted"), f["properties"].get("elderly_pop_sum")]
        for f in ranked[:10]
    ]
    artifacts.append(_table_artifact("top_townships", ["街道", "可达性指数", "老年人口"], rows))
    content = (
        f"E2SFCA 分析完成（{mode}，阈值 {thresholds} 分钟，衰减 {decay}）："
        f"需求网格 {len(grid['features'])} 个，设施 {len(facilities)} 个；"
        f"已输出网格/街道/区级可达性图层。"
    )
    return ToolResult(tool_call_id="", name="e2sca_analysis", content=content, artifacts=artifacts)


@register_tool(
    "supply_demand_summary",
    "Summarize elderly care supply-demand matching by township: covered beds and beds per 1000 elderly",
    SupplyDemandParams,
)
def supply_demand_summary(
    facility_dataset: str = "beijing_nursing_homes",
    region: str = "beijing",
    catchment_min: float = 30.0,
    walking_speed_kmh: float = 4.2,
) -> ToolResult:
    _check_region(region)
    grid = _demand_grid_for_region(region, 1.0)
    points = grid_centroids(grid)["features"]
    facilities = _load_facilities(facility_dataset, "beds", 100.0)
    registry = default_registry()
    townships = registry.get("beijing_townships")
    if townships is None:
        return ToolResult(
            tool_call_id="", name="supply_demand_summary",
            content="缺少街道边界数据集 beijing_townships",
            is_error=True,
        )
    aggregated = aggregate_by_polygons(
        {"type": "FeatureCollection", "features": points},
        townships,
        value_field="elderly_pop",
        polygon_id_field="name",
    )
    polygon_times: dict[str, dict[str, float]] = {}
    network = get_walking_network()
    facility_nodes: list[Optional[Any]] = []
    reachable: dict[Any, dict[Any, float]] = {}
    if network.available:
        facility_nodes = [network.snap(f.lon, f.lat) for f in facilities]
        reachable = network.travel_times(facility_nodes, catchment_min)
    for feat in aggregated["features"]:
        geom = feat.get("geometry")
        if geom is None:
            continue
        from shapely.geometry import shape

        centroid = shape(geom).centroid
        name = feat["properties"].get("name")
        if network.available:
            node = network.snap(centroid.x, centroid.y)
            polygon_times[str(name)] = {
                f.id: reachable.get(fn, {}).get(node)
                for f, fn in zip(facilities, facility_nodes)
            }
        else:
            polygon_times[str(name)] = {
                f.id: euclidean_travel_min(centroid.x, centroid.y, f.lon, f.lat, walking_speed_kmh)
                for f in facilities
            }
    summary = supply_demand_by_polygons(
        aggregated, facilities, polygon_times, catchment_min, polygon_id_field="name"
    )
    ranked = sorted(
        summary["features"],
        key=lambda f: (f["properties"].get("beds_per_1000") is None, f["properties"].get("beds_per_1000") or 0),
    )
    rows = [
        [
            f["properties"].get("name"),
            f["properties"].get("district"),
            f["properties"].get("elderly_pop_sum"),
            f["properties"].get("supply_beds"),
            f["properties"].get("beds_per_1000"),
        ]
        for f in ranked[:15]
    ]
    mode = "network" if network.available else "euclidean"
    content = (
        f"供需匹配完成（{mode}，{catchment_min} 分钟步行圈）："
        f"共 {len(summary['features'])} 个街道，已输出覆盖床位与千人床位数，"
        f"缺口最大的街道见表格。"
    )
    return ToolResult(
        tool_call_id="", name="supply_demand_summary", content=content,
        artifacts=[
            Artifact(kind="geojson", name="supply_demand", data=summary),
            _table_artifact(
                "supply_demand_gaps",
                ["街道", "区", "老年人口", "覆盖床位", "千人床位数"],
                rows,
            ),
        ],
    )


def get_accessibility_tools() -> list[Any]:
    names = (
        "describe_dataset",
        "build_demand_grid",
        "nearest_facility",
        "isochrone",
        "e2sca_analysis",
        "supply_demand_summary",
    )
    return get_tools(*names)
