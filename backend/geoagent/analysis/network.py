"""步行路网封装：graphml 加载、节点吸附、最短步行时间、等时圈。

- 有路网（data/road/beijing_walk_{nodes,edges}.csv）时用 networkx Dijkstra 计算真实路网时间；
- 无路网时工具层会回退到直线距离 × 步行速度的估计值，并在结果中明确标注。
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import networkx as nx
import pandas as pd
from shapely.geometry import Point
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_NODES_CSV = ROOT / "data" / "road" / "beijing_walk_nodes.csv"
DEFAULT_EDGES_CSV = ROOT / "data" / "road" / "beijing_walk_edges.csv"


def _haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, [lon1, lat1, lon2, lat2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def euclidean_travel_min(
    lon1: float, lat1: float, lon2: float, lat2: float, speed_kmh: float
) -> float:
    """直线距离估计的步行时间（分钟）。"""
    return _haversine_km(lon1, lat1, lon2, lat2) / max(speed_kmh, 0.1) * 60.0


def _polygon_area_km2(poly: Any) -> float:
    minx, miny, maxx, maxy = poly.bounds
    mid_lat = (miny + maxy) / 2
    lon_scale = 111_320.0 * max(math.cos(math.radians(mid_lat)), 0.01)
    return abs(poly.area) * 111_320.0 * lon_scale / 1e6


class WalkingNetwork:
    """步行路网（只读，加载后用于吸附与最短时间计算）。"""

    def __init__(
        self,
        nodes_csv: Optional[Path | str] = None,
        edges_csv: Optional[Path | str] = None,
    ) -> None:
        self.nodes_path = Path(nodes_csv) if nodes_csv else DEFAULT_NODES_CSV
        self.edges_path = Path(edges_csv) if edges_csv else DEFAULT_EDGES_CSV
        self.graph: Optional[nx.DiGraph] = None
        self._tree: Optional[STRtree] = None
        self._node_ids: list[Any] = []
        self.available = self.nodes_path.exists() and self.edges_path.exists()
        if self.available:
            self._load()

    def _load(self) -> None:
        nodes = pd.read_csv(self.nodes_path)
        edges = pd.read_csv(self.edges_path)
        self.graph = nx.DiGraph()
        node_attrs = {
            int(row.node_id): {
                "x": float(row.x),
                "y": float(row.y),
                "lon": float(row.x),
                "lat": float(row.y),
            }
            for row in nodes.itertuples()
        }
        self.graph.add_nodes_from(node_attrs)
        nx.set_node_attributes(self.graph, node_attrs)
        self.graph.add_edges_from(
            (
                int(row.u),
                int(row.v),
                {
                    "travel_min": float(row.travel_min),
                    "length_m": float(row.length_m),
                    "highway": str(row.highway),
                    "name": str(row.name),
                },
            )
            for row in edges.itertuples()
        )
        self._node_ids = list(self.graph.nodes)
        points = [
            Point(node_attrs[n]["x"], node_attrs[n]["y"])
            for n in self._node_ids
        ]
        self._tree = STRtree(points)

    def snap(self, lon: float, lat: float) -> Optional[Any]:
        """返回距离给定经纬度最近的路网节点 id。"""
        if self._tree is None:
            return None
        idx = self._tree.nearest(Point(lon, lat))
        return self._node_ids[idx]

    def travel_times(
        self, facility_nodes: list[Any], threshold_min: float
    ) -> dict[Any, dict[Any, float]]:
        """每个设施节点到阈值内各节点的最短步行时间（分钟）。"""
        result: dict[Any, dict[Any, float]] = {}
        if self.graph is None:
            return result
        for node in facility_nodes:
            if node not in self.graph:
                result[node] = {}
                continue
            lengths = nx.single_source_dijkstra_path_length(
                self.graph, node, cutoff=threshold_min, weight="travel_min"
            )
            result[node] = {n: round(t, 3) for n, t in lengths.items()}
        return result

    def isochrone_polygons(
        self,
        lon: float,
        lat: float,
        thresholds: list[float],
        speed_kmh: float = 4.2,
    ) -> list[dict[str, Any]]:
        """等时圈：返回 [{threshold_min, area_km2, polygon_geojson}]。"""
        from shapely.geometry import MultiPoint

        results: list[dict[str, Any]] = []
        node = self.snap(lon, lat)
        for thr in sorted(thresholds):
            if node is not None and self.graph is not None:
                lengths = nx.single_source_dijkstra_path_length(
                    self.graph, node, cutoff=thr, weight="travel_min"
                )
                points = [
                    Point(self.graph.nodes[n]["x"], self.graph.nodes[n]["y"])
                    for n in lengths
                ]
            else:
                # 无路网：按直线速度画圆兜底。
                radius_km = thr * max(speed_kmh, 0.1) / 60.0
                lon_scale = 111.32 * max(math.cos(math.radians(lat)), 0.01)
                points = [
                    Point(
                        lon + radius_km * math.cos(a) / lon_scale,
                        lat + radius_km * math.sin(a) / 111.32,
                    )
                    for a in (math.tau * k / 16 for k in range(16))
                ]
            if len(points) < 3:
                continue
            hull = MultiPoint(points).convex_hull
            if hull.is_empty or hull.geom_type not in ("Polygon", "MultiPolygon"):
                continue
            results.append(
                {
                    "threshold_min": thr,
                    "area_km2": round(_polygon_area_km2(hull), 3),
                    "polygon": hull.__geo_interface__,
                }
            )
        return results


@lru_cache(maxsize=2)
def get_walking_network(
    nodes_csv: Optional[str] = None, edges_csv: Optional[str] = None
) -> WalkingNetwork:
    """进程级只读缓存的路网实例。"""
    return WalkingNetwork(nodes_csv, edges_csv)
