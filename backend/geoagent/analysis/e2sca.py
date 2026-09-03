"""E2SFCA（增强两步移动搜索法）可达性指数与供需匹配。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

import geopandas as gpd
import pandas as pd


@dataclass
class DemandPoint:
    """需求点（1km 网格质心）。"""

    id: str
    lon: float
    lat: float
    elderly_pop: float


@dataclass
class Facility:
    """设施点（养老机构）。"""

    id: str
    lon: float
    lat: float
    capacity: float
    name: str = ""


def decay_weight(
    t_min: float,
    thresholds: list[float],
    decay: str = "gaussian",
    band_weights: Optional[list[float]] = None,
) -> float:
    """时间衰减权重。

    - gaussian：w = exp(-0.5 * (t / t_max)^2)，超过最大阈值返回 0；
    - step：按阈值分档，默认档位权重 [1.0, 0.68, 0.22, ...]。
    """
    if t_min > max(thresholds):
        return 0.0
    if decay == "step":
        bands = sorted(thresholds)
        weights = band_weights or [1.0, 0.68, 0.22] + [0.1] * max(len(bands) - 3, 0)
        for i, thr in enumerate(bands):
            if t_min <= thr:
                return weights[min(i, len(weights) - 1)]
        return 0.0
    t_max = max(thresholds)
    return math.exp(-0.5 * (t_min / t_max) ** 2)


def e2sca_accessibility(
    demand: list[DemandPoint],
    facilities: list[Facility],
    times: dict[str, dict[str, float]],
    thresholds: list[float],
    decay: str = "gaussian",
    band_weights: Optional[list[float]] = None,
) -> list[dict[str, Any]]:
    """计算 E2SFCA 可达性指数。

    times: facility_id -> {demand_id: 步行时间（分钟）}，仅含最大阈值内的配对。
    第一步：设施供需比 R_j = S_j / Σ_i(D_i × W(t_ij))；
    第二步：需求点可达性 A_i = Σ_j(R_j × W(t_ij))。
    """
    if not thresholds:
        raise ValueError("至少需要一个时间阈值")
    supply_ratio: dict[str, float] = {}
    for facility in facilities:
        denominator = 0.0
        facility_times = times.get(facility.id, {})
        for point in demand:
            t = facility_times.get(point.id)
            if t is None:
                continue
            denominator += point.elderly_pop * decay_weight(
                t, thresholds, decay, band_weights
            )
        supply_ratio[facility.id] = (
            facility.capacity / denominator if denominator > 0 else 0.0
        )

    results: list[dict[str, Any]] = []
    for point in demand:
        accessibility = 0.0
        served = 0
        min_time = float("inf")
        for facility in facilities:
            t = times.get(facility.id, {}).get(point.id)
            if t is None:
                continue
            weight = decay_weight(t, thresholds, decay, band_weights)
            if weight <= 0:
                continue
            accessibility += supply_ratio[facility.id] * weight
            served += 1
            min_time = min(min_time, t)
        results.append(
            {
                "demand_id": point.id,
                "accessibility": round(accessibility, 6),
                "min_travel_min": round(min_time, 2) if served else None,
                "served_facilities": served,
            }
        )
    return results


def attach_accessibility_to_grid(
    grid_fc: dict[str, Any], results: list[dict[str, Any]]
) -> dict[str, Any]:
    """把 E2SFCA 结果合并进网格要素属性。"""
    by_id = {r["demand_id"]: r for r in results}
    for feat in grid_fc.get("features", []):
        cell_id = feat.get("properties", {}).get("cell_id")
        row = by_id.get(str(cell_id))
        if row:
            feat["properties"]["accessibility"] = row["accessibility"]
            feat["properties"]["min_travel_min"] = row["min_travel_min"]
            feat["properties"]["served_facilities"] = row["served_facilities"]
    return grid_fc


def aggregate_accessibility_by_polygons(
    grid_fc: dict[str, Any], polygons_fc: dict[str, Any], polygon_id_field: str
) -> dict[str, Any]:
    """按街道/区聚合：老年人口加权可达性、需求总量、网格数。"""
    points = gpd.GeoDataFrame.from_features(grid_fc["features"], crs="EPSG:4326")
    polygons = gpd.GeoDataFrame.from_features(
        polygons_fc["features"], crs="EPSG:4326"
    )
    points = points[
        (points["accessibility"].notna())
        & (points["elderly_pop"] > 0)
    ]
    joined = gpd.sjoin(points, polygons, how="inner", predicate="within")
    if joined.empty:
        raise ValueError("网格未落在目标面内，无法聚合")
    group_key = (
        f"{polygon_id_field}_right"
        if f"{polygon_id_field}_right" in joined.columns
        else polygon_id_field
    )

    def _weighted_agg(df: Any) -> pd.Series:
        pop = float(df["elderly_pop"].sum())
        return pd.Series(
            {
                "elderly_pop_sum": pop,
                "grid_count": int(len(df)),
                "accessibility_weighted": (
                    float((df["accessibility"] * df["elderly_pop"]).sum() / pop)
                    if pop > 0
                    else 0.0
                ),
            }
        )

    agg = (
        joined.groupby(group_key)
        .apply(_weighted_agg, include_groups=False)
        .reset_index()
    )
    merged = polygons.merge(agg, left_on=polygon_id_field, right_on=group_key, how="inner")
    features = []
    for _, row in merged.iterrows():
        props = {
            k: row[k]
            for k in merged.columns
            if k not in ("geometry", group_key)
            and (isinstance(row[k], (int, float, str, bool)) or row[k] is None)
        }
        props["accessibility_weighted"] = round(float(row["accessibility_weighted"]), 6)
        features.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": row.geometry.__geo_interface__,
            }
        )
    return {"type": "FeatureCollection", "features": features}


def supply_demand_by_polygons(
    polygons_fc: dict[str, Any],
    facilities: list[Facility],
    polygon_facility_times: dict[str, dict[str, float]],
    catchment_min: float,
    polygon_id_field: str = "polygon_id",
) -> dict[str, Any]:
    """按街道/区聚合供需：需求总量、覆盖床位、千人床位数、缺口。

    polygon_facility_times: polygon_id -> {facility_id: 步行时间}。
    """
    polygons = gpd.GeoDataFrame.from_features(
        polygons_fc["features"], crs="EPSG:4326"
    )
    for _, row in polygons.iterrows():
        pid = str(row.get(polygon_id_field))
        demand = float(row.get("elderly_pop_sum") or 0.0)
        times = polygon_facility_times.get(pid, {})
        supply = sum(
            f.capacity
            for f in facilities
            if (t := times.get(f.id)) is not None and t <= catchment_min
        )
        beds_per_1000 = supply / demand * 1000.0 if demand > 0 else None
        polygons.loc[row.name, "supply_beds"] = round(supply, 2)
        polygons.loc[row.name, "beds_per_1000"] = (
            round(beds_per_1000, 2) if beds_per_1000 is not None else None
        )
    features = []
    for _, row in polygons.iterrows():
        props = {
            k: row[k]
            for k in polygons.columns
            if k != "geometry"
            and (isinstance(row[k], (int, float, str, bool)) or row[k] is None)
        }
        features.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": row.geometry.__geo_interface__,
            }
        )
    return {"type": "FeatureCollection", "features": features}
