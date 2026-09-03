"""地理演示工具（纯 Python 实现，暂不依赖 PyQGIS）。

这些工具用于演示工具/artifact 协议——结果如何在会话窗口中渲染。
后续可用运行在 worker 进程中的 PyQGIS 工具替换/扩充它们。
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from ..analysis.datasets import DEMO_DATASETS, default_registry
from .registry import get_tools, register_tool
from .result import Artifact, ToolResult


class ListDatasetsParams(BaseModel):
    pass


class LoadDatasetParams(BaseModel):
    dataset_id: str = Field(description="Dataset id from list_datasets")


class BufferPointParams(BaseModel):
    lon: float = Field(description="Center longitude (WGS84)")
    lat: float = Field(description="Center latitude (WGS84)")
    radius_km: float = Field(gt=0, description="Buffer radius in kilometers")


class PolygonAreaParams(BaseModel):
    coordinates: list[list[float]] = Field(
        min_length=3,
        description="Ring coordinates as [[lon, lat], ...] (WGS84, planar approx.)",
    )


class DistanceParams(BaseModel):
    from_coord: list[float] = Field(
        min_length=2, max_length=2, description="Start point [lon, lat]"
    )
    to_coord: list[float] = Field(
        min_length=2, max_length=2, description="End point [lon, lat]"
    )


def _feature_collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [f["lon"], f["lat"]],
                },
                "properties": {k: v for k, v in f.items() if k not in ("lon", "lat")},
            }
            for f in features
        ],
    }


def _circle_geojson(lon: float, lat: float, radius_km: float) -> dict[str, Any]:
    n = 64
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    points = [
        [lon + dlon * math.cos(2 * math.pi * i / n), lat + dlat * math.sin(2 * math.pi * i / n)]
        for i in range(n)
    ]
    points.append(points[0])
    area_km2 = math.pi * radius_km * radius_km
    return {
        "type": "Feature",
        "properties": {"radius_km": radius_km, "area_km2": round(area_km2, 3)},
        "geometry": {"type": "Polygon", "coordinates": [points]},
    }


@register_tool("list_datasets", "List available demo geospatial datasets", ListDatasetsParams)
def list_datasets() -> ToolResult:
    rows = default_registry().list()
    content = "可用数据集:\n" + "\n".join(
        f"- {r['id']}: {r['name']} ({r['features']} 个要素)" for r in rows
    )
    return ToolResult(
        tool_call_id="",
        name="list_datasets",
        content=content,
        artifacts=[
            Artifact(
                kind="table",
                name="datasets",
                data={"columns": ["id", "name", "description", "features"], "rows": rows},
            )
        ],
    )


@register_tool("load_dataset", "Load a demo dataset as GeoJSON features", LoadDatasetParams)
def load_dataset(dataset_id: str) -> ToolResult:
    ds = DEMO_DATASETS.get(dataset_id)
    if ds is None:
        # 尝试加载 data/ 下的预备数据集。
        geojson = default_registry().get(dataset_id)
        if geojson is None:
            return ToolResult(
                tool_call_id="",
                name="load_dataset",
                content=f"数据集不存在: {dataset_id}",
                is_error=True,
            )
        name = dataset_id
        count = len(geojson["features"])
    else:
        geojson = _feature_collection(ds["features"])
        name = ds["name"]
        count = len(ds["features"])
    return ToolResult(
        tool_call_id="",
        name="load_dataset",
        content=f"已加载数据集 {name}，共 {count} 个要素。",
        artifacts=[Artifact(kind="geojson", name=dataset_id, data=geojson)],
    )


@register_tool("buffer_point", "Create a circular buffer around a point", BufferPointParams)
def buffer_point(lon: float, lat: float, radius_km: float) -> ToolResult:
    geojson = _circle_geojson(lon, lat, radius_km)
    area_km2 = geojson["properties"]["area_km2"]
    return ToolResult(
        tool_call_id="",
        name="buffer_point",
        content=(
            f"已生成以 ({lon:.4f}, {lat:.4f}) 为中心、半径 {radius_km} km 的缓冲区，"
            f"面积约 {area_km2} km²。"
        ),
        artifacts=[Artifact(kind="geojson", name="buffer", data=geojson)],
    )


def _shoelace_area_km2(coordinates: list[list[float]]) -> float:
    ring = coordinates + [coordinates[0]]
    area = 0.0
    for (x1, y1), (x2, y2) in zip(ring[:-1], ring[1:]):
        area += x1 * y2 - x2 * y1
    area = abs(area) / 2.0
    return area * (111.0 * 111.0)  # 小范围平面近似


@register_tool("polygon_area", "Compute the area of a polygon ring (km²)", PolygonAreaParams)
def polygon_area(coordinates: list[list[float]]) -> ToolResult:
    if len(coordinates) < 3:
        return ToolResult(
            tool_call_id="",
            name="polygon_area",
            content="多边形至少需要 3 个顶点",
            is_error=True,
        )
    area_km2 = round(_shoelace_area_km2(coordinates), 3)
    return ToolResult(
        tool_call_id="",
        name="polygon_area",
        content=f"多边形面积约 {area_km2} km²（平面近似，适用于小范围）。",
        artifacts=[
            Artifact(
                kind="geojson",
                name="polygon",
                data={
                    "type": "Feature",
                    "properties": {"area_km2": area_km2},
                    "geometry": {"type": "Polygon", "coordinates": [coordinates]},
                },
            )
        ],
    )


def _haversine_km(a: list[float], b: list[float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


@register_tool(
    "distance_between_points",
    "Compute the great-circle distance between two points (km)",
    DistanceParams,
)
def distance_between_points(from_coord: list[float], to_coord: list[float]) -> ToolResult:
    distance_km = round(_haversine_km(from_coord, to_coord), 3)
    return ToolResult(
        tool_call_id="",
        name="distance_between_points",
        content=f"两点间大圆距离约 {distance_km} km。",
        artifacts=[
            Artifact(
                kind="geojson",
                name="distance_line",
                data={
                    "type": "Feature",
                    "properties": {"distance_km": distance_km},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [from_coord, to_coord],
                    },
                },
            )
        ],
    )


def get_geo_tools() -> list[Any]:
    names = (
        "list_datasets",
        "load_dataset",
        "buffer_point",
        "polygon_area",
        "distance_between_points",
    )
    return get_tools(*names)
