"""把 sql/sys_menu.json 里的「Bot管理」菜单补进已初始化过的数据库。

种子数据只在 sys_menu 为空时导入，已有数据的库（本机开发库、已部署的 NAS）不会自动拿到新菜单，
故用此脚本按 route_name 补齐缺少的节点；已存在的节点不改动，可重复执行。

用法（backend 目录）：uv run python -m app.plugin.module_bot.seed_menu --env=dev
"""

import argparse
import asyncio
import json
import os

ROOT_ROUTE_NAME = "Bot"


async def main() -> None:
    from sqlalchemy import select

    from app.config.path_conf import SCRIPT_DIR
    from app.core.base_model import MappedBase
    from app.core.database import async_db_session, async_engine
    from app.modules.system.menu.model import MenuModel
    from app.utils.import_util import ImportUtil

    ImportUtil.find_models(MappedBase)  # 加载全部模型，关联关系（created_by 等）才能解析

    menus = json.loads((SCRIPT_DIR / "sys_menu.json").read_text(encoding="utf-8"))
    root = next(m for m in menus if m.get("route_name") == ROOT_ROUTE_NAME)

    async with async_db_session() as db, db.begin():

        async def ensure(node: dict, parent_id: int | None) -> None:
            data = {k: v for k, v in node.items() if k != "children"}
            # 目录/页面按 route_name 唯一识别；按钮没有 route_name，按父节点 + 权限标识识别
            if data.get("route_name"):
                where = MenuModel.route_name == data["route_name"]
            else:
                where = (MenuModel.parent_id == parent_id) & (MenuModel.permission == data["permission"])
            obj = (await db.execute(select(MenuModel).where(where, MenuModel.is_deleted.is_(False)))).scalars().first()
            if obj is None:
                obj = MenuModel(**data, parent_id=parent_id)
                db.add(obj)
                await db.flush()
                print(f"新增菜单: {data['name']}")
            for child in node.get("children", []):
                await ensure(child, obj.id)

        await ensure(root, None)

    await async_engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default="dev")
    os.environ["ENVIRONMENT"] = parser.parse_args().env
    asyncio.run(main())
