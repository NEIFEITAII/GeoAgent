"""分析核心单元测试（合成数据，不依赖真实数据 / 网络 / 密钥）。"""

from __future__ import annotations

import pytest

from geoagent.analysis.e2sca import (
    DemandPoint,
    Facility,
    attach_accessibility_to_grid,
    decay_weight,
    e2sca_accessibility,
)
from geoagent.analysis.grid import aggregate_by_polygons, build_demand_grid, load_census
from geoagent.analysis.network import WalkingNetwork, euclidean_travel_min


def _box_feature(name: str, minx: float, miny: float, maxx: float, maxy: float) -> dict:
    return {
        "type": "Feature",
        "properties": {"name": name},
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [minx, miny],
                    [maxx, miny],
                    [maxx, maxy],
                    [minx, maxy],
                    [minx, miny],
                ]
            ],
        },
    }


DISTRICTS_FC = {
    "type": "FeatureCollection",
    "features": [
        _box_feature("东城区", 116.38, 39.86, 116.45, 39.98),
        _box_feature("西城区", 116.31, 39.87, 116.38, 39.98),
    ],
}


def test_load_census_uses_city_rate_fallback(tmp_path):
    csv_path = tmp_path / "census.csv"
    csv_path.write_text(
        "district,population_2020,p60plus_share_pct,note\n"
        "北京市,1000,20.0,城市行\n"
        "东城区,500,25.0,\n"
        "西城区,300,,缺比例\n",
        encoding="utf-8",
    )
    census = load_census(csv_path)
    assert census["东城区"]["elderly_pop"] == pytest.approx(125.0)
    assert census["西城区"]["elderly_pop"] == pytest.approx(60.0)


def test_build_demand_grid_splits_by_area():
    census = {
        "东城区": {"population": 800000.0, "elderly_pop": 200000.0},
        "西城区": {"population": 400000.0, "elderly_pop": 100000.0},
    }
    grid = build_demand_grid(DISTRICTS_FC, census, cell_size_km=2.0)
    assert grid["type"] == "FeatureCollection"
    assert grid["features"]
    total = sum(f["properties"]["elderly_pop"] for f in grid["features"])
    assert total == pytest.approx(300000.0, rel=0.05)
    for feat in grid["features"]:
        props = feat["properties"]
        assert {"cell_id", "district", "elderly_pop", "lon", "lat"} <= set(props)
        assert props["district"] in ("东城区", "西城区")


def test_e2sca_supply_and_accessibility():
    demand = [
        DemandPoint(id="d1", lon=0, lat=0, elderly_pop=100),
        DemandPoint(id="d2", lon=0, lat=0, elderly_pop=50),
    ]
    facilities = [Facility(id="f1", lon=0, lat=0, capacity=100)]
    times = {"f1": {"d1": 10.0, "d2": 25.0}}
    results = e2sca_accessibility(demand, facilities, times, [30], decay="gaussian")
    assert results[0]["served_facilities"] == 1
    assert results[0]["accessibility"] > 0
    results_step = e2sca_accessibility(demand, facilities, times, [15], decay="step")
    assert results_step[0]["accessibility"] > 0
    assert results_step[1]["accessibility"] == 0
    assert results_step[1]["served_facilities"] == 0


def test_decay_weight_bounds():
    assert decay_weight(0.0, [15, 30], "gaussian") == 1.0
    assert decay_weight(31.0, [15, 30], "gaussian") == 0.0
    assert decay_weight(10.0, [15, 30], "step") == 1.0
    assert decay_weight(20.0, [15, 30], "step") == pytest.approx(0.68)
    assert decay_weight(31.0, [15, 30], "step") == 0.0


def test_attach_accessibility_to_grid():
    grid = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"cell_id": 1}, "geometry": None},
        ],
    }
    results = [
        {
            "demand_id": "1",
            "accessibility": 0.5,
            "min_travel_min": 12.0,
            "served_facilities": 2,
        }
    ]
    out = attach_accessibility_to_grid(grid, results)
    assert out["features"][0]["properties"]["accessibility"] == 0.5
    assert out["features"][0]["properties"]["min_travel_min"] == 12.0


def test_aggregate_by_polygons():
    points = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"elderly_pop": 10},
                "geometry": {"type": "Point", "coordinates": [116.40, 39.90]},
            },
            {
                "type": "Feature",
                "properties": {"elderly_pop": 20},
                "geometry": {"type": "Point", "coordinates": [116.41, 39.91]},
            },
        ],
    }
    polys = {
        "type": "FeatureCollection",
        "features": [_box_feature("东城区", 116.38, 39.86, 116.45, 39.98)],
    }
    out = aggregate_by_polygons(
        points, polys, value_field="elderly_pop", polygon_id_field="name"
    )
    props = out["features"][0]["properties"]
    assert props["elderly_pop_sum"] == 30
    assert props["point_count"] == 2


def test_walking_network_fallback_and_euclidean():
    net = WalkingNetwork(
        nodes_csv="nonexistent_nodes.csv", edges_csv="nonexistent_edges.csv"
    )
    assert not net.available
    t = euclidean_travel_min(116.0, 40.0, 116.1, 40.0, 4.2)
    import math

    expected = 0.1 * 111.32 * math.cos(math.radians(40.0)) / 4.2 * 60.0
    assert t == pytest.approx(expected, rel=0.05)
    polys = net.isochrone_polygons(116.0, 40.0, [30], speed_kmh=4.2)
    assert polys
    assert polys[0]["threshold_min"] == 30
    assert polys[0]["area_km2"] > 0
