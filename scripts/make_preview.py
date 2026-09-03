"""生成 data/preview.html：北京养老可达性数据的离线预览页。

用法：
    backend/.venv/Scripts/python.exe scripts/make_preview.py

预览页使用 OpenLayers（通过 CDN 加载，打开页面时需要联网），
内嵌简化后的区划 / 街道 / 机构点位图层，便于快速审阅数据。
"""

from __future__ import annotations

import json
from pathlib import Path

from shapely.geometry import mapping, shape

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def load(rel_path: str) -> dict:
    return json.loads((DATA_DIR / rel_path).read_text(encoding="utf-8"))


def simplify_fc(fc: dict, tolerance: float) -> dict:
    """按容差简化 GeoJSON 要素（用于减小预览页体积）。"""
    features = []
    for feat in fc["features"]:
        geom = shape(feat["geometry"])
        simplified = geom.simplify(tolerance, preserve_topology=True)
        if simplified.is_empty:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": feat["properties"],
                "geometry": mapping(simplified),
            }
        )
    return {"type": "FeatureCollection", "features": features}


def main() -> int:
    districts = simplify_fc(load("admin/beijing_districts.geojson"), 0.0002)
    townships = simplify_fc(load("admin/beijing_townships.geojson"), 0.0004)
    facilities = load("facilities/beijing_nursing_homes.geojson")

    template = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>北京养老可达性分析 - 数据预览</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/ol@9.2.4/ol.css">
<script src="https://cdn.jsdelivr.net/npm/ol@9.2.4/dist/ol.js"></script>
<style>
  html,body{margin:0;height:100%;font-family:'Microsoft YaHei',sans-serif}
  #map{position:absolute;inset:0}
  #panel{position:absolute;top:10px;left:10px;z-index:10;background:rgba(255,255,255,.92);padding:10px 14px;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.25);font-size:13px;line-height:1.9}
  #panel label{cursor:pointer}
  .dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:4px;vertical-align:-1px}
</style>
</head>
<body>
<div id="panel">
  <b>北京养老可达性数据预览</b><br>
  <label><span class="dot" style="background:#1a73e8"></span><input type="checkbox" id="ckDist" checked> 16 区边界</label><br>
  <label><span class="dot" style="background:#f9ab00"></span><input type="checkbox" id="ckTown" checked> 街道/乡镇（__TOWN_COUNT__）</label><br>
  <label><span class="dot" style="background:#d93025"></span><input type="checkbox" id="ckFac" checked> 养老机构（__FAC_COUNT__）</label>
</div>
<div id="map"></div>
<script>
const DIST = __DIST__;
const TOWN = __TOWN__;
const FAC  = __FAC__;
const map = new ol.Map({
  target: 'map',
  layers: [ new ol.layer.Tile({ source: new ol.source.OSM() }) ],
  view: new ol.View({ center: [12960000, 4520000], zoom: 9 })
});
function vlayer(geojson, style, key) {
  const source = new ol.source.Vector({ features: new ol.format.GeoJSON().readFeatures(geojson, { featureProjection: 'EPSG:3857' }) });
  const layer = new ol.layer.Vector({ source: source, style: style });
  layer.set(key, true);
  map.addLayer(layer);
  return layer;
}
const distLayer = vlayer(DIST, new ol.style.Style({
  stroke: new ol.style.Stroke({ color: '#1a73e8', width: 2 }),
  fill: new ol.style.Fill({ color: 'rgba(26,115,232,0.08)' })
}), 'dist');
const townLayer = vlayer(TOWN, new ol.style.Style({
  stroke: new ol.style.Stroke({ color: '#f9ab00', width: 0.8 })
}), 'town');
const facLayer = vlayer(FAC, new ol.style.Style({
  image: new ol.style.Circle({ radius: 5, fill: new ol.style.Fill({ color: '#d93025' }), stroke: new ol.style.Stroke({ color: '#ffffff', width: 1.5 }) })
}), 'fac');
document.getElementById('ckDist').onchange = e => distLayer.setVisible(e.target.checked);
document.getElementById('ckTown').onchange = e => townLayer.setVisible(e.target.checked);
document.getElementById('ckFac').onchange  = e => facLayer.setVisible(e.target.checked);
const ext = distLayer.getSource().getExtent();
if (ext) map.getView().fit(ext, { padding: [30, 30, 30, 30], maxZoom: 12 });
</script>
</body>
</html>"""

    html = (
        template.replace("__DIST__", json.dumps(districts, ensure_ascii=False))
        .replace("__TOWN__", json.dumps(townships, ensure_ascii=False))
        .replace("__FAC__", json.dumps(facilities, ensure_ascii=False))
        .replace("__TOWN_COUNT__", str(len(townships["features"])))
        .replace("__FAC_COUNT__", str(len(facilities["features"])))
    )
    out_path = DATA_DIR / "preview.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"已生成 {out_path.relative_to(ROOT)}（{out_path.stat().st_size} 字节）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
