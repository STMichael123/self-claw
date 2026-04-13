"""应用入口与生命周期管理 — 对应 SPEC §18 src/app。"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.api.routes import router
from src.storage.database import get_connection

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：启动时初始化资源，关闭时清理。"""
    load_dotenv()

    # 初始化数据库
    db_path = os.environ.get("DATABASE_PATH", "data/self_claw.db")
    db = get_connection(db_path)
    app.state.db = db

    yield

    # 关闭资源
    db.close()


def create_app() -> FastAPI:
    """工厂函数 — 创建并配置 FastAPI 应用。"""
    app = FastAPI(
        title="Self-Claw",
        description="轻量级企业 Agent 框架",
        version="0.6.0",
        lifespan=lifespan,
    )
    app.include_router(router)

    # 静态文件与前端 SPA
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(str(WEB_DIR / "index.html"))

    return app


app = create_app()
