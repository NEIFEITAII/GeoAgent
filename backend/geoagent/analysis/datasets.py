"""数据集注册表：统一管理内置演示数据集与 data/ 下预备的真实数据集。

设计说明：
- 演示数据集 id 稳定（beijing_pois / beijing_subway），避免破坏既有行为；
- 预备数据集从仓库根目录 data/ 按相对路径扫描，文件缺失时自动跳过；
- LLM 只通过 dataset_id 引用数据，不接触文件路径。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_DIR = ROOT / "data"

# 内置演示数据集（id 保持稳定）。
DEMO_DATASETS: dict[str, dict[str, Any]] = {
    "beijing_pois": {
        "name": "北京兴趣点示例",
        "description": "北京市区 6 个示例兴趣点（学校、医院、公园、商场等）",
        "source": "内置演示",
        "features": [
            {"name": "北京大学", "category": "教育", "lon": 116.312, "lat": 39.992},
            {"name": "协和医院", "category": "医疗", "lon": 116.417, "lat": 39.914},
            {"name": "朝阳公园", "category": "公园", "lon": 116.479, "lat": 39.933},
            {"name": "国贸商城", "category": "商业", "lon": 116.459, "lat": 39.909},
            {"name": "故宫博物院", "category": "文化", "lon": 116.397, "lat": 39.917},
            {"name": "北京南站", "category": "交通", "lon": 116.378, "lat": 39.865},
        ],
    },
    "beijing_subway": {
        "name": "北京地铁站点示例",
        "description": "北京 6 个示例地铁站点",
        "source": "内置演示",
        "features": [
            {"name": "天安门东", "line": "1号线", "lon": 116.407, "lat": 39.908},
            {"name": "西单", "line": "1号线", "lon": 116.374, "lat": 39.907},
            {"name": "国贸", "line": "1号线", "lon": 116.459, "lat": 39.909},
            {"name": "中关村", "line": "4号线", "lon": 116.317, "lat": 39.982},
            {"name": "北京南站", "line": "4号线", "lon": 116.378, "lat": 39.865},
            {"name": "朝阳门", "line": "2号线", "lon": 116.434, "lat": 39.923},
        ],
    },
}

# data/ 下的预备数据集（相对仓库根目录 data/）。
PREPARED_DATASETS: dict[str, dict[str, str]] = {
    "beijing_districts": {
        "rel_path": "admin/beijing_districts.geojson",
        "name": "北京16区边界",
        "description": "北京市 16 个市辖区边界（DataV，WGS84）",
    },
    "beijing_townships": {
        "rel_path": "admin/beijing_townships.geojson",
        "name": "北京街道/乡镇边界",
        "description": "北京市 342 个街道/乡镇边界（OSM admin_level=8，含区归属）",
    },
    "beijing_nursing_homes": {
        "rel_path": "facilities/beijing_nursing_homes.geojson",
        "name": "北京养老机构点位",
        "description": "北京市域内养老机构点位（OSM，12 个，覆盖有限）",
    },
}


def _feature_collection(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """把演示数据行（含 lon/lat）转为 FeatureCollection。"""
    features = []
    for row in rows:
        props = {k: v for k, v in row.items() if k not in ("lon", "lat")}
        features.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": {
                    "type": "Point",
                    "coordinates": [row["lon"], row["lat"]],
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


class DatasetRegistry:
    """数据集注册表。

    data_dir 可注入（测试时指向临时目录）；默认扫描仓库根目录 data/。
    """

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self.data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
        self._geojson_cache: dict[str, dict[str, Any]] = {}

    def list(self) -> list[dict[str, Any]]:
        """返回全部可用数据集元数据。"""
        rows: list[dict[str, Any]] = []
        for ds_id, ds in DEMO_DATASETS.items():
            rows.append(
                {
                    "id": ds_id,
                    "name": ds["name"],
                    "description": ds["description"],
                    "features": len(ds["features"]),
                    "source": ds.get("source", "内置演示"),
                }
            )
        for ds_id, ds in PREPARED_DATASETS.items():
            path = self.data_dir / ds["rel_path"]
            if not path.exists():
                continue
            fc = self._load_geojson(ds_id, path)
            rows.append(
                {
                    "id": ds_id,
                    "name": ds["name"],
                    "description": ds["description"],
                    "features": len(fc["features"]),
                    "source": str(path.relative_to(self.data_dir.parent.parent)),
                }
            )
        return rows

    def get(self, dataset_id: str) -> Optional[dict[str, Any]]:
        """按 id 返回 GeoJSON FeatureCollection。"""
        if dataset_id in DEMO_DATASETS:
            return _feature_collection(DEMO_DATASETS[dataset_id]["features"])
        prepared = PREPARED_DATASETS.get(dataset_id)
        if prepared is None:
            return None
        path = self.data_dir / prepared["rel_path"]
        if not path.exists():
            return None
        return self._load_geojson(dataset_id, path)

    def describe(self, dataset_id: str) -> Optional[dict[str, Any]]:
        """返回数据集概览（要素数、几何类型、bbox、字段）。"""
        fc = self.get(dataset_id)
        if fc is None:
            return None
        features = fc.get("features", [])
        geom_types: dict[str, int] = {}
        fields: set[str] = set()
        lons: list[float] = []
        lats: list[float] = []
        for feat in features:
            geom = feat.get("geometry") or {}
            gtype = geom.get("type", "None")
            geom_types[gtype] = geom_types.get(gtype, 0) + 1
            fields.update(feat.get("properties", {}).keys())
            coords = _geom_points(geom)
            if coords:
                lons.extend(c[0] for c in coords)
                lats.extend(c[1] for c in coords)
        return {
            "id": dataset_id,
            "feature_count": len(features),
            "geometry_types": geom_types,
            "fields": sorted(fields),
            "bbox": (
                [round(min(lons), 5), round(min(lats), 5), round(max(lons), 5), round(max(lats), 5)]
                if lons
                else None
            ),
        }

    def _load_geojson(self, dataset_id: str, path: Path) -> dict[str, Any]:
        if dataset_id not in self._geojson_cache:
            self._geojson_cache[dataset_id] = json.loads(
                path.read_text(encoding="utf-8")
            )
        return self._geojson_cache[dataset_id]


def _geom_points(geom: dict[str, Any]) -> list[list[float]]:
    """提取几何的所有坐标点（用于 bbox 计算）。"""
    gtype = geom.get("type")
    coords = geom.get("coordinates", [])
    if gtype == "Point":
        return [coords]
    if gtype in ("LineString", "MultiPoint"):
        return list(coords)
    if gtype == "Polygon":
        return [pt for ring in coords for pt in ring]
    if gtype == "MultiLineString":
        return [pt for line in coords for pt in line]
    if gtype == "MultiPolygon":
        return [pt for poly in coords for ring in poly for pt in ring]
    if gtype == "GeometryCollection":
        points = []
        for g in coords:
            points.extend(_geom_points(g))
        return points
    return []


_DEFAULT_REGISTRY: Optional[DatasetRegistry] = None


def default_registry() -> DatasetRegistry:
    """进程级默认注册表（只读派生数据，非会话状态）。"""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = DatasetRegistry()
    return _DEFAULT_REGISTRY
