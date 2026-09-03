"""共享空间分析核心（数据集注册 / 1km 网格需求 / 步行路网 / E2SFCA）。"""

from .datasets import DatasetRegistry, default_registry
from .grid import aggregate_by_polygons, build_demand_grid, load_census
from .network import WalkingNetwork, get_walking_network

__all__ = [
    "DatasetRegistry",
    "default_registry",
    "WalkingNetwork",
    "get_walking_network",
    "build_demand_grid",
    "aggregate_by_polygons",
    "load_census",
]
