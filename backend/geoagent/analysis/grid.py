"""1km 网格需求生成与空间聚合。

需求侧口径：老年人口 = 区级常住人口 × 区级 60+ 比例（七普），
再按网格与区界相交面积比例分摊到每个 1km 网格。
缺少区级比例时使用全市平均比例（19.6%），该近似口径在 README 中已说明。
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Optional

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CENSUS_PATH = ROOT / "data" / "population" / "beijing_census_2020.csv"
BEIJING_UTM = "EPSG:32650"  # UTM 50N，覆盖北京全域


def load_census(path: Optional[Path] = None) -> dict[str, dict[str, float]]:
    """读取七普人口 CSV，返回 {区名: {population, elderly_pop}}。

    60+ 比例缺失的区使用全市平均比例。
    """
    csv_path = path or DEFAULT_CENSUS_PATH
    rows: list[dict[str, str]] = []
    if csv_path.exists():
        with open(csv_path, encoding="utf-8-sig") as fp:
            rows = list(csv.DictReader(fp))
    city_rate = 19.6
    for row in rows:
        if row.get("district") == "北京市":
            try:
                city_rate = float(row["p60plus_share_pct"])
            except (TypeError, ValueError):
                pass
    result: dict[str, dict[str, float]] = {}
    for row in rows:
        district = row.get("district", "")
        if not district or district == "北京市":
            continue
        try:
            population = float(row.get("population_2020") or 0)
        except ValueError:
            population = 0.0
        try:
            rate = float(row.get("p60plus_share_pct") or city_rate)
        except ValueError:
            rate = city_rate
        result[district] = {
            "population": population,
            "elderly_pop": round(population * rate / 100.0, 2),
        }
    return result


def build_demand_grid(
    districts_fc: dict[str, Any],
    census: Optional[dict[str, dict[str, float]]] = None,
    cell_size_km: float = 1.0,
    name_field: str = "name",
) -> dict[str, Any]:
    """在区界范围内生成 1km 网格，并按面积分摊老年人口。

    返回 GeoJSON FeatureCollection（Polygon 网格，属性含
    cell_id / district / elderly_pop / cell_area_km2 / lon / lat）。
    """
    census = census if census is not None else load_census()
    districts = gpd.GeoDataFrame.from_features(
        districts_fc["features"], crs="EPSG:4326"
    ).to_crs(BEIJING_UTM)
    districts["area_m2"] = districts.geometry.area

    def _elderly(name: str) -> float:
        info = census.get(name)
        return info["elderly_pop"] if info else 0.0

    districts["elderly_pop"] = districts[name_field].map(_elderly)
    districts = districts[districts["elderly_pop"] > 0]
    if districts.empty:
        raise ValueError("区划数据与人口数据无法匹配，未生成需求网格")

    minx, miny, maxx, maxy = districts.total_bounds
    size = max(cell_size_km * 1000.0, 100.0)
    xs = np.arange(minx, maxx + size, size)
    ys = np.arange(miny, maxy + size, size)
    x_starts = xs[:-1] if len(xs) > 1 else xs
    y_starts = ys[:-1] if len(ys) > 1 else ys
    cells = gpd.GeoDataFrame(
        {
            "cell_id": range(len(x_starts) * len(y_starts)),
            "geometry": [
                box(x, y, x + size, y + size)
                for y in y_starts
                for x in x_starts
            ],
        },
        crs=BEIJING_UTM,
    )
    if cells.empty:
        raise ValueError("网格为空")

    joined = gpd.sjoin(cells, districts, how="inner", predicate="intersects")
    if joined.empty:
        raise ValueError("网格与区划无交集")
    # 求每个（网格 × 区）的交集面积，按区老年人口分摊。
    joined = joined.reset_index(drop=True)
    right_geometry = districts.loc[joined["index_right"], "geometry"].reset_index(
        drop=True
    )
    inter = joined.geometry.intersection(right_geometry, align=False)
    joined["inter_area_m2"] = inter.area
    joined["share"] = (joined["inter_area_m2"] / joined["area_m2"]).clip(upper=1.0)
    joined["cell_elderly"] = joined["share"] * joined["elderly_pop"]
    joined["cell_area_km2"] = joined["inter_area_m2"] / 1e6

    primary = (
        joined.sort_values("share", ascending=False)
        .groupby("cell_id")[name_field]
        .first()
        .rename("district")
    )
    sums = (
        joined.groupby("cell_id")
        .agg(
            elderly_pop=("cell_elderly", "sum"),
            cell_area_km2=("cell_area_km2", "sum"),
        )
        .join(primary)
        .reset_index()
    )
    sums = sums[sums["elderly_pop"] > 0]
    if sums.empty:
        raise ValueError("人口分摊后网格为空")

    keep_cells = cells.merge(sums, on="cell_id", how="inner").to_crs("EPSG:4326")
    features = []
    for _, row in keep_cells.iterrows():
        centroid = row.geometry.centroid
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "cell_id": int(row["cell_id"]),
                    "district": row["district"],
                    "elderly_pop": round(float(row["elderly_pop"]), 2),
                    "cell_area_km2": round(float(row["cell_area_km2"]), 4),
                    "lon": round(centroid.x, 6),
                    "lat": round(centroid.y, 6),
                },
                "geometry": row.geometry.__geo_interface__,
            }
        )
    return {"type": "FeatureCollection", "features": features}


def grid_centroids(grid_fc: dict[str, Any]) -> dict[str, Any]:
    """把网格面要素转为质心点要素（用于路网吸附与 E2SFCA 需求点）。"""
    features = []
    for feat in grid_fc.get("features", []):
        geom = feat.get("geometry")
        if geom is None or geom["type"] != "Polygon":
            continue
        props = dict(feat.get("properties", {}))
        from shapely.geometry import shape

        centroid = shape(geom).centroid
        props["lon"] = round(centroid.x, 6)
        props["lat"] = round(centroid.y, 6)
        features.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": {
                    "type": "Point",
                    "coordinates": [centroid.x, centroid.y],
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def aggregate_by_polygons(
    points_fc: dict[str, Any],
    polygons_fc: dict[str, Any],
    value_field: str,
    polygon_id_field: str,
) -> dict[str, Any]:
    """把点要素按面归属聚合（质心落在面内），返回带汇总字段的面要素。"""
    points = gpd.GeoDataFrame.from_features(points_fc["features"], crs="EPSG:4326")
    polygons = gpd.GeoDataFrame.from_features(
        polygons_fc["features"], crs="EPSG:4326"
    )
    joined = gpd.sjoin(points, polygons, how="inner", predicate="within")
    if joined.empty:
        raise ValueError("点要素未落在任何目标面内，无法聚合")
    group_key = (
        f"{polygon_id_field}_right"
        if f"{polygon_id_field}_right" in joined.columns
        else polygon_id_field
    )
    agg = (
        joined.groupby(group_key)
        .agg(
            **{
                f"{value_field}_sum": (value_field, "sum"),
                "point_count": (value_field, "count"),
            }
        )
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
        features.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": row.geometry.__geo_interface__,
            }
        )
    return {"type": "FeatureCollection", "features": features}
