"""本地冒烟：验证土地变化检测库连接与受控 SQL 工具层（不调用 LLM）。

用法（需先在 backend/.env 配好 GEOAGENT_PG_DSN）：
    backend/.venv/Scripts/python.exe scripts/smoke_pg.py

覆盖：连接池 → list_tables → describe_table → run_sql（含护栏拒绝示例）。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 允许从仓库任意目录运行：把 backend 加入导入路径。
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from geoagent.config import Settings  # noqa: E402
from geoagent.tools.pg import PgGateway, PgGuardError  # noqa: E402


async def main() -> int:
    settings = Settings()
    if not settings.pg_dsn:
        print("未配置 GEOAGENT_PG_DSN，请先在 backend/.env 填写。")
        return 2
    gateway = PgGateway(
        dsn=settings.pg_dsn,
        whitelist=settings.pg_whitelist,
        max_rows=settings.pg_max_rows,
        timeout_s=settings.pg_timeout_s,
    )
    try:
        tables = await gateway.list_tables()
        print("== list_tables ==")
        for t in tables:
            print(f"  - {t['quoted']}: {t['description'] or '无描述'}")

        for t in tables[:1]:
            info = await gateway.describe_table(t["quoted"])
            print(f"\n== describe_table {t['quoted']} ==")
            for c in info["columns"][:10]:
                print(f"  - {c['column_name']}: {c['data_type']} (nullable={c['is_nullable']})")
            print(f"  ... 共 {len(info['columns'])} 列")

        result = await gateway.run_sql(
            'SELECT "OBJECTID", "XMC", "DLMC", "TBLX", "MJ" '
            'FROM data."2026_1_change_landuse" '
            'ORDER BY "MJ" DESC LIMIT 3',
            conversation_id="smoke_pg",
        )
        print(f"\n== run_sql 图斑表（面积最大前3）== 返回 {result['row_count']} 行")
        print("  columns:", result["columns"])
        for row in result["rows"][:3]:
            print("  ", row)
        print("\n== 护栏示例（应被拒绝）==")
        try:
            await gateway.run_sql("SELECT * FROM public.secret_table")
        except PgGuardError as exc:
            print("  已拒绝:", exc)
        return 0
    finally:
        await gateway.close()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(asyncio.run(main()))
