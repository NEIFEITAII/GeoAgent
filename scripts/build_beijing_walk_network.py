"""从北京 OSM PBF 构建步行路网（graphml + 边线预览 GeoJSON）。

用法：
    backend/.venv/Scripts/python.exe scripts/build_beijing_walk_network.py

产物：
- data/road/beijing_walk_nodes.csv      步行路网节点表（node_id, x, y）
- data/road/beijing_walk_edges.csv      步行路网边表（u, v, travel_min, ...）
- data/road/beijing_walk_edges.geojson  边线预览（可在 QGIS 中打开）
- data/road/beijing_walk_stats.json     构建统计

默认步行速度 4.2 km/h（70 m/min），偏老年人步行速度，可通过参数调整。
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import networkx as nx
import pyrosm
from pyproj import Transformer

ROOT = Path(__file__).resolve().parent.parent
PBF_PATH = ROOT / "data" / "road" / "beijing.osm.pbf"
OUT_NODES = ROOT / "data" / "road" / "beijing_walk_nodes.csv"
OUT_EDGE_TABLE = ROOT / "data" / "road" / "beijing_walk_edges.csv"
OUT_EDGES = ROOT / "data" / "road" / "beijing_walk_edges.geojson"
OUT_STATS = ROOT / "data" / "road" / "beijing_walk_stats.json"

DEFAULT_SPEED_KMH = 4.2
_TRANSFORMER = Transformer.from_crs("EPSG:4326", "EPSG:32650", always_xy=True)


def _haversine_m(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """两点间的球面距离（米）。"""
    lon1, lat1 = map(math.radians, p1)
    lon2, lat2 = map(math.radians, p2)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371000.0 * 2 * math.asin(math.sqrt(a))


def _line_length_m(coords: list[tuple[float, float]]) -> float:
    """用 UTM 投影计算折线长度（米），比端点大圆距离更准。"""
    xs, ys = _TRANSFORMER.transform(
        [c[0] for c in coords], [c[1] for c in coords]
    )
    return sum(
        math.hypot(xs[i + 1] - xs[i], ys[i + 1] - ys[i])
        for i in range(len(xs) - 1)
    )


def _split_lines(geom: Any) -> list[Any]:
    """把 LineString / MultiLineString 统一拆成 LineString 列表。"""
    if geom is None:
        return []
    if geom.geom_type == "LineString":
        return [geom]
    if geom.geom_type == "MultiLineString":
        return list(geom.geoms)
    return []


def build_graph(edges_gdf: Any, speed_kmh: float) -> nx.DiGraph:
    """从 pyrosm 边表构建双向步行图。"""
    graph = nx.DiGraph()
    speed_m_min = speed_kmh * 1000.0 / 60.0
    node_ids: dict[tuple[float, float], int] = {}

    def node_id(coord: tuple[float, float]) -> int:
        key = (round(coord[0], 6), round(coord[1], 6))
        if key not in node_ids:
            idx = len(node_ids)
            node_ids[key] = idx
            graph.add_node(idx, x=key[0], y=key[1], lon=key[0], lat=key[1])
        return node_ids[key]

    for _, row in edges_gdf.iterrows():
        attrs: dict[str, Any] = {
            "highway": str(row.get("highway") or ""),
            "name": str(row.get("name") or ""),
        }
        for line in _split_lines(row.geometry):
            coords = list(line.coords)
            if len(coords) < 2:
                continue
            length_m = _line_length_m(coords)
            travel_min = max(length_m / speed_m_min, 1e-6)
            edge_attrs = {
                **attrs,
                "length_m": round(length_m, 2),
                "travel_min": round(travel_min, 4),
            }
            for a, b in zip(coords[:-1], coords[1:]):
                u = node_id(a)
                v = node_id(b)
                if u != v:
                    # 步行默认双向通行；若已有同向边则取更短时间。
                    if graph.has_edge(u, v) and graph[u][v]["travel_min"] <= travel_min:
                        continue
                    graph.add_edge(u, v, **edge_attrs)
                    graph.add_edge(v, u, **edge_attrs)
    return graph


def main() -> int:
    """构建并保存步行路网。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if not PBF_PATH.exists():
        print(f"缺少 PBF 文件: {PBF_PATH}", file=sys.stderr)
        return 1
    print(f"读取 {PBF_PATH.name} ...")
    osm = pyrosm.OSM(str(PBF_PATH))
    edges = osm.get_network(network_type="walking")
    print(f"步行路段数: {len(edges)}")

    graph = build_graph(edges, DEFAULT_SPEED_KMH)
    print(
        f"图规模: nodes={graph.number_of_nodes()} edges={graph.number_of_edges()}"
    )

    components = list(nx.weakly_connected_components(graph))
    sizes = sorted((len(c) for c in components), reverse=True)
    print(f"弱连通分量: {len(components)}，最大分量 {sizes[0] if sizes else 0} 节点")

    OUT_NODES.parent.mkdir(parents=True, exist_ok=True)
    import csv

    with open(OUT_NODES, "w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        writer.writerow(["node_id", "x", "y"])
        for node, attrs in graph.nodes(data=True):
            writer.writerow([node, attrs["x"], attrs["y"]])
    with open(OUT_EDGE_TABLE, "w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        writer.writerow(["u", "v", "travel_min", "length_m", "highway", "name"])
        for u, v, attrs in graph.edges(data=True):
            writer.writerow(
                [
                    u,
                    v,
                    attrs["travel_min"],
                    attrs["length_m"],
                    attrs["highway"],
                    attrs["name"],
                ]
            )

    features = []
    for _, row in edges.iterrows():
        lines = _split_lines(row.geometry)
        if not lines:
            continue
        coords_all = [list(line.coords) for line in lines]
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "highway": str(row.get("highway") or ""),
                    "name": str(row.get("name") or ""),
                },
                "geometry": {
                    "type": (
                        "LineString" if len(coords_all) == 1 else "MultiLineString"
                    ),
                    "coordinates": (
                        coords_all[0] if len(coords_all) == 1 else coords_all
                    ),
                },
            }
        )
    (OUT_EDGES.parent / OUT_EDGES.name).write_text(
        json.dumps(
            {"type": "FeatureCollection", "features": features}, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    stats = {
        "pbf": PBF_PATH.name,
        "walking_speed_kmh": DEFAULT_SPEED_KMH,
        "edge_rows": len(edges),
        "graph_nodes": graph.number_of_nodes(),
        "graph_edges": graph.number_of_edges(),
        "weak_components": len(components),
        "largest_component_nodes": sizes[0] if sizes else 0,
    }
    OUT_STATS.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"完成: {OUT_NODES.relative_to(ROOT)} / {OUT_EDGE_TABLE.relative_to(ROOT)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
