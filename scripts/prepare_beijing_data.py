"""准备北京市养老可达性分析基础数据（可重复执行）。

职责：
1. 从 Overpass API 拉取北京市养老机构点位（OSM 标签）并转换为 GeoJSON；
2. 从 Overpass API 拉取北京市街道/乡镇边界（admin_level=7）并转换为 GeoJSON；
3. 保留原始 Overpass 响应到 data/_raw/，便于追溯。

用法：
    python scripts/prepare_beijing_data.py

依赖：shapely（后端环境已通过 uv add shapely 安装）。
输出目录为 data/（已被 .gitignore 忽略）。
"""

from __future__ import annotations

import json
import math
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import polygonize, unary_union

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "_raw"
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]
TIMEOUT_SECONDS = 600
USER_AGENT = "GeoAgent-data-prep/0.1"

# 北京行政区（省/直辖市级）区域选择，用于把查询限定在市域内。
BEIJING_AREA = (
    'area["name:zh"="北京市"]["boundary"="administrative"]["admin_level"="4"]->.a;'
)
BEIJING_BBOX = "39.39,115.40,41.10,117.53"

NURSING_HOME_QUERY = f"""
[out:json][timeout:180];
{BEIJING_AREA}
(
  nwr(area.a)["amenity"="social_facility"]["social_facility"="nursing_home"];
  nwr(area.a)["amenity"="nursing_home"];
  nwr(area.a)["amenity"="retirement_home"];
);
out center tags;
"""

NURSING_HOME_BBOX_QUERY = f"""
[out:json][timeout:180];
(
  nwr({BEIJING_BBOX})["amenity"="social_facility"]["social_facility"="nursing_home"];
  nwr({BEIJING_BBOX})["amenity"="nursing_home"];
  nwr({BEIJING_BBOX})["amenity"="retirement_home"];
);
out center tags;
"""

TOWNSHIP_QUERY = f"""
[out:json][timeout:600][maxsize:1073741824];
{BEIJING_AREA}
rel(area.a)["boundary"="administrative"]["admin_level"="8"];
out geom;
"""

TOWNSHIP_BBOX_QUERY = f"""
[out:json][timeout:600][maxsize:1073741824];
rel["boundary"="administrative"]["admin_level"="8"]({BEIJING_BBOX});
out geom;
"""

DISTRICTS_QUERY = f"""
[out:json][timeout:600][maxsize:1073741824];
{BEIJING_AREA}
rel(area.a)["boundary"="administrative"]["admin_level"="6"];
out geom;
"""

DISTRICTS_BBOX_QUERY = f"""
[out:json][timeout:600][maxsize:1073741824];
rel["boundary"="administrative"]["admin_level"="6"]({BEIJING_BBOX});
out geom;
"""

INSPECT_QUERY = f"""
[out:json][timeout:300];
{BEIJING_AREA}
rel(area.a)["boundary"="administrative"];
out tags;
"""

# 北京与河北交界的个别开发区在 OSM 中也被标成 admin_level=8，但行政上不属于北京。
BLACKLIST_TOWNSHIPS = {"香河经济开发区", "廊坊经济技术开发区"}


def query_overpass(query: str) -> dict[str, Any]:
    """POST 到 Overpass API 并返回解析后的 JSON（自动切换镜像）。"""
    body = urllib.parse.urlencode({"data": query}).encode("utf-8")
    last_error: Optional[Exception] = None
    for endpoint in OVERPASS_URLS:
        try:
            req = urllib.request.Request(endpoint, data=body, method="POST")
            req.add_header("User-Agent", USER_AGENT)
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                    raw = resp.read()
            except urllib.error.HTTPError as exc:
                # Overpass 的错误可能以 HTTP 状态返回，但正文仍是 JSON。
                raw = exc.read()
            payload = json.loads(raw.decode("utf-8"))
            remark = str(payload.get("remark", ""))
            if "runtime error" in remark.lower() or remark.lower().startswith("error"):
                raise RuntimeError(f"Overpass 返回错误: {remark[:500]}")
            return payload
        except Exception as exc:  # noqa: BLE001 - 镜像逐个尝试，保留最后一个错误
            last_error = exc
            print(f"  镜像 {endpoint} 失败: {exc}", file=sys.stderr)
    assert last_error is not None
    raise last_error


def save_raw(payload: dict[str, Any], name: str) -> Path:
    """保存原始 Overpass 响应。"""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def nursing_homes_geojson(payload: dict[str, Any]) -> dict[str, Any]:
    """把机构点位 Overpass 响应转换为 GeoJSON 点要素。"""
    features: list[dict[str, Any]] = []
    for el in payload.get("elements", []):
        tags = el.get("tags", {})
        center = el.get("center")
        if center is None and "lat" in el:
            center = {"lat": el["lat"], "lon": el["lon"]}
        if center is None:
            continue
        props: dict[str, Any] = {
            "osm_id": f"{el.get('type', 'node')}/{el['id']}",
            "name": tags.get("name", ""),
        }
        for key, value in tags.items():
            if key not in props:
                props[key] = value
        features.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": {
                    "type": "Point",
                    "coordinates": [center["lon"], center["lat"]],
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def _geometry_from_relation(el: dict[str, Any]) -> Optional[dict[str, Any]]:
    """用 shapely 把 OSM 边界 relation 的 member ways 组装成 Polygon / MultiPolygon。"""
    lines: list[LineString] = []
    for member in el.get("members", []):
        if member.get("type") != "way":
            continue
        geometry = member.get("geometry")
        if not geometry:
            continue
        coords = [(pt["lon"], pt["lat"]) for pt in geometry]
        if len(coords) >= 2:
            lines.append(LineString(coords))
    if not lines:
        return None
    merged = unary_union(lines)
    polygons = [poly for poly in polygonize(merged) if not poly.is_empty]
    if not polygons:
        return None
    final_shape = unary_union(polygons)
    if final_shape.geom_type == "Polygon":
        return {
            "type": "Polygon",
            "coordinates": [list(final_shape.exterior.coords)]
            + [list(ring.coords) for ring in final_shape.interiors],
        }
    if final_shape.geom_type == "MultiPolygon":
        return {
            "type": "MultiPolygon",
            "coordinates": [
                [list(poly.exterior.coords)]
                + [list(ring.coords) for ring in poly.interiors]
                for poly in final_shape.geoms
            ],
        }
    return None


def _to_shapely(geom: dict[str, Any]) -> Optional[Polygon | MultiPolygon]:
    """把 GeoJSON Polygon / MultiPolygon 转成 shapely 几何。"""
    if geom["type"] == "Polygon":
        return Polygon(geom["coordinates"][0], geom["coordinates"][1:])
    if geom["type"] == "MultiPolygon":
        return MultiPolygon(
            [Polygon(poly[0], poly[1:]) for poly in geom["coordinates"]]
        )
    return None


def _geojson_area_km2(geom: dict[str, Any]) -> float:
    """等距圆柱近似面积（仅用于信息展示，不做分析计算）。"""
    shape = _to_shapely(geom)
    if shape is None or shape.is_empty:
        return 0.0
    minx, miny, maxx, maxy = shape.bounds
    mid_lat = (miny + maxy) / 2
    lon_scale = 111_320.0 * max(math.cos(math.radians(mid_lat)), 0.01)
    return abs(shape.area) * 111_320.0 * lon_scale / 1e6


def townships_geojson(payload: dict[str, Any]) -> dict[str, Any]:
    """把街道/乡镇边界 Overpass 响应转换为 GeoJSON 面要素。"""
    features: list[dict[str, Any]] = []
    skipped = 0
    for el in payload.get("elements", []):
        if el.get("type") != "relation":
            continue
        geometry = _geometry_from_relation(el)
        if geometry is None:
            skipped += 1
            continue
        tags = el.get("tags", {})
        name = tags.get("name") or tags.get("name:zh") or ""
        props: dict[str, Any] = {
            "osm_id": f"relation/{el['id']}",
            "name": name,
            "admin_level": tags.get("admin_level", "7"),
            "area_km2": round(_geojson_area_km2(geometry), 3),
        }
        for key, value in tags.items():
            if key not in props:
                props[key] = value
        features.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": geometry,
            }
        )
    if skipped:
        print(f"  提示: {skipped} 个边界关系因几何不完整被跳过", file=sys.stderr)
    return {"type": "FeatureCollection", "features": features}


def assign_districts(
    townships: dict[str, Any], districts: dict[str, Any]
) -> dict[str, Any]:
    """用代表点包含关系把街道/乡镇归属到北京 16 区，并过滤市域外要素。"""
    buckets: list[tuple[str, Optional[Polygon | MultiPolygon]]] = []
    for feat in districts.get("features", []):
        name = feat.get("properties", {}).get("name", "")
        if name and feat.get("geometry"):
            buckets.append((name, _to_shapely(feat["geometry"])))
    kept: list[dict[str, Any]] = []
    dropped = 0
    for feat in townships.get("features", []):
        if feat.get("properties", {}).get("name") in BLACKLIST_TOWNSHIPS:
            dropped += 1
            continue
        shape = _to_shapely(feat.get("geometry"))
        if shape is None or shape.is_empty:
            dropped += 1
            continue
        # representative_point 保证落在面内，避免边界街道质心漂移误判。
        pt = shape.representative_point()
        district = next(
            (
                dname
                for dname, dshape in buckets
                if dshape is not None and dshape.contains(pt)
            ),
            None,
        )
        if district is None:
            dropped += 1
            continue
        feat["properties"]["district"] = district
        kept.append(feat)
    if dropped:
        print(f"  过滤: {dropped} 个要素不在北京市域内", file=sys.stderr)
    townships["features"] = kept
    return townships


def filter_to_beijing(
    geojson: dict[str, Any], districts: dict[str, Any]
) -> dict[str, Any]:
    """只保留代表点落在北京区划多边形内的要素（用于 bbox 兜底结果清洗）。"""
    buckets: list[Polygon | MultiPolygon] = []
    for feat in districts.get("features", []):
        if feat.get("geometry"):
            shape = _to_shapely(feat["geometry"])
            if shape is not None:
                buckets.append(shape)
    kept: list[dict[str, Any]] = []
    dropped = 0
    for feat in geojson.get("features", []):
        shape = _to_shapely(feat.get("geometry"))
        if shape is not None and not shape.is_empty and any(
            dshape.contains(shape.representative_point()) for dshape in buckets
        ):
            kept.append(feat)
        else:
            dropped += 1
    if dropped:
        print(f"  过滤: {dropped} 个要素不在北京市域内", file=sys.stderr)
    geojson["features"] = kept
    return geojson


def filter_points_to_beijing(
    geojson: dict[str, Any], districts: dict[str, Any]
) -> dict[str, Any]:
    """只保留落在北京区划内的点要素（用于 bbox 查询结果的清洗）。"""
    buckets: list[Polygon | MultiPolygon] = []
    for feat in districts.get("features", []):
        if feat.get("geometry"):
            shape = _to_shapely(feat["geometry"])
            if shape is not None:
                buckets.append(shape)
    kept: list[dict[str, Any]] = []
    dropped = 0
    for feat in geojson.get("features", []):
        geometry = feat.get("geometry")
        name = feat.get("properties", {}).get("name", "")
        if geometry and geometry["type"] == "Point" and "方舱" not in name:
            pt = Point(geometry["coordinates"])
            if any(shape.contains(pt) for shape in buckets):
                kept.append(feat)
                continue
        dropped += 1
    if dropped:
        print(f"  过滤: {dropped} 个点要素不在北京市域内", file=sys.stderr)
    geojson["features"] = kept
    return geojson


def _fetch_with_fallback(
    primary_query: str,
    fallback_query: str,
    name: str,
    converter: Any,
    rel_out: str,
    post: Any = None,
) -> bool:
    """先按市域 area 查询，失败或为空时用 bbox 兜底。"""
    payload: Optional[dict[str, Any]] = None
    for label, query in (("area", primary_query), ("bbox", fallback_query)):
        try:
            print(f"[{name}] 使用 {label} 查询 ...")
            candidate = query_overpass(query)
            if candidate.get("elements"):
                payload = candidate
                break
            print(f"[{name}] {label} 查询无结果，尝试兜底。")
        except Exception as exc:
            print(f"[{name}] {label} 查询失败: {exc}", file=sys.stderr)
    if payload is None:
        print(f"[{name}] 全部查询失败，保留现有数据不变。", file=sys.stderr)
        return False
    save_raw(payload, f"{name}.json")
    geojson = converter(payload)
    if post is not None:
        geojson = post(geojson)
    out_path = DATA_DIR / rel_out
    out_path.write_text(json.dumps(geojson, ensure_ascii=False), encoding="utf-8")
    print(
        f"[{name}] 完成: {len(geojson['features'])} 个要素 -> {out_path.relative_to(ROOT)}"
    )
    return True


def inspect_admin_levels() -> int:
    """检查北京市域内 OSM 行政边界的 admin_level 分布。"""
    payload = query_overpass(INSPECT_QUERY)
    by_level: dict[str, int] = {}
    for el in payload.get("elements", []):
        level = el.get("tags", {}).get("admin_level", "?")
        by_level[level] = by_level.get(level, 0) + 1
    print("admin_level 分布:", dict(sorted(by_level.items(), key=lambda kv: kv[0])))
    for level in sorted(by_level, key=lambda v: (len(v), v)):
        print(f"\n--- admin_level={level} ---")
        for el in payload.get("elements", []):
            tags = el.get("tags", {})
            if tags.get("admin_level") == level:
                print(
                    f"  {el['type']}/{el['id']} "
                    f"name={tags.get('name')!r} name:zh={tags.get('name:zh')!r}"
                )
    return 0


def main() -> int:
    """执行全部数据准备任务。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) > 1 and sys.argv[1] == "inspect":
        return inspect_admin_levels()
    districts_path = DATA_DIR / "admin" / "beijing_districts.geojson"
    districts_fc: Optional[dict[str, Any]] = None
    if districts_path.exists():
        districts_fc = json.loads(districts_path.read_text(encoding="utf-8"))

    # 优先使用 OSM 区级边界（与街道边界同源，归属更准）作为归属依据。
    osm_districts_path = DATA_DIR / "admin" / "beijing_districts_osm.geojson"
    osm_districts_fc: Optional[dict[str, Any]] = None
    raw_districts = RAW_DIR / "beijing_osm_districts.json"
    process_only = len(sys.argv) > 1 and sys.argv[1] == "process-only"
    if process_only and raw_districts.exists():
        raw = json.loads(raw_districts.read_text(encoding="utf-8"))
        osm_districts_fc = townships_geojson(raw)
        osm_districts_path.write_text(
            json.dumps(osm_districts_fc, ensure_ascii=False), encoding="utf-8"
        )
    else:
        fetched = _fetch_with_fallback(
            DISTRICTS_QUERY,
            DISTRICTS_BBOX_QUERY,
            "beijing_osm_districts",
            townships_geojson,
            "admin/beijing_districts_osm.geojson",
            None,
        )
        if fetched and osm_districts_path.exists():
            osm_districts_fc = json.loads(
                osm_districts_path.read_text(encoding="utf-8")
            )
            # bbox 兜底可能混入河北县级区划，用 DataV 区划再清洗一次。
            if districts_fc is not None and len(osm_districts_fc["features"]) > 16:
                osm_districts_fc = filter_to_beijing(
                    osm_districts_fc, districts_fc
                )
                osm_districts_path.write_text(
                    json.dumps(osm_districts_fc, ensure_ascii=False),
                    encoding="utf-8",
                )

    def post_townships(geojson: dict[str, Any]) -> dict[str, Any]:
        if osm_districts_fc is not None:
            return assign_districts(geojson, osm_districts_fc)
        if districts_fc is None:
            print("  提示: 缺少区划文件，跳过区归属过滤", file=sys.stderr)
            return geojson
        return assign_districts(geojson, districts_fc)

    def post_nursing_homes(geojson: dict[str, Any]) -> dict[str, Any]:
        source = osm_districts_fc or districts_fc
        if source is None:
            return geojson
        return filter_points_to_beijing(geojson, source)

    tasks = [
        (
            "beijing_nursing_homes",
            NURSING_HOME_QUERY,
            NURSING_HOME_BBOX_QUERY,
            nursing_homes_geojson,
            "facilities/beijing_nursing_homes.geojson",
            post_nursing_homes,
        ),
        (
            "beijing_townships",
            TOWNSHIP_QUERY,
            TOWNSHIP_BBOX_QUERY,
            townships_geojson,
            "admin/beijing_townships.geojson",
            post_townships,
        ),
    ]
    results = []
    for name, primary, fallback, converter, rel_out, post in tasks:
        if process_only and (RAW_DIR / f"{name}.json").exists():
            payload = json.loads((RAW_DIR / f"{name}.json").read_text("utf-8"))
            geojson = converter(payload)
            if post is not None:
                geojson = post(geojson)
            out_path = DATA_DIR / rel_out
            out_path.write_text(
                json.dumps(geojson, ensure_ascii=False), encoding="utf-8"
            )
            print(
                f"[{name}] 仅本地处理: {len(geojson['features'])} 个要素"
                f" -> {out_path.relative_to(ROOT)}"
            )
            results.append(True)
            continue
        results.append(
            _fetch_with_fallback(
                primary, fallback, name, converter, rel_out, post
            )
        )
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
