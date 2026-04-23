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
from src.channels.adapter import ChannelRegistry, TestChannelAdapter
from src.config import get_settings
from src.services.agent_service import AgentService
from src.services.file_workspace import FileWorkspaceService
from src.services.hook_service import HookRegistry
from src.services.memory import MemoryService
from src.services.notification import NotificationService
from src.services.scheduler import SchedulerService
from src.services.skill_service import SkillService
from src.services.task_service import TaskService
from src.storage.database import get_connection

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：启动时初始化资源，关闭时清理。"""
    load_dotenv()
    get_settings.cache_clear()
    settings = get_settings()
    settings.sandbox_root_path.mkdir(parents=True, exist_ok=True)
    settings.skill_root_path.mkdir(parents=True, exist_ok=True)
    settings.principle_file_path.parent.mkdir(parents=True, exist_ok=True)
    settings.long_term_memory_dir_path.mkdir(parents=True, exist_ok=True)
    Path("data/memory/archives").mkdir(parents=True, exist_ok=True)
    Path(".agents/hooks").mkdir(parents=True, exist_ok=True)

    # 初始化数据库
    db = get_connection(settings.database_path)
    app.state.db = db
    app.state.settings = settings
    app.state.skill_service = SkillService(db, skill_root=settings.skill_root_path)
    app.state.skill_service.reload_catalog()
    app.state.memory_service = MemoryService(
        data_dir=settings.memory_data_dir,
        principle_file=str(settings.principle_file_path),
        long_term_dir=str(settings.long_term_memory_dir_path),
        db=db,
    )
    app.state.memory_service.sync_principle_index()
    app.state.memory_service.sync_long_term_index()
    app.state.file_workspace_service = FileWorkspaceService(
        db,
        sandbox_root=settings.file_sandbox_root,
        protected_roots=[settings.skill_root_path],
        read_max_bytes=settings.file_read_max_bytes,
        write_max_bytes=settings.file_write_max_bytes,
        lock_timeout_sec=settings.file_lock_timeout_sec,
    )
    app.state.channel_registry = ChannelRegistry()
    app.state.channel_registry.register("test", TestChannelAdapter())
    app.state.notification_service = NotificationService(app.state.channel_registry)
    app.state.hook_registry = HookRegistry()
    app.state.hook_registry.discover_hooks(settings.skill_root_path.parent / "hooks")
    app.state.scheduler_service = SchedulerService()
    app.state.scheduler_service.start()
    app.state.agent_service = AgentService(
        db,
        skill_service=app.state.skill_service,
        memory_service=app.state.memory_service,
        file_workspace_service=app.state.file_workspace_service,
        notification_service=app.state.notification_service,
        hook_registry=app.state.hook_registry,
        max_parallel_sub_agents=settings.max_parallel_sub_agents,
        max_parallel_main_runs=settings.max_parallel_main_runs,
        model_name=os.environ.get("LLM_MODEL", "gpt-4o"),
    )
    app.state.task_service = TaskService(
        db,
        scheduler=app.state.scheduler_service,
        agent_service=app.state.agent_service,
    )
    app.state.task_service.bootstrap()

    yield

    # 关闭资源
    app.state.scheduler_service.shutdown()
    db.close()


def create_app() -> FastAPI:
    """工厂函数 — 创建并配置 FastAPI 应用。"""
    get_settings.cache_clear()
    settings = get_settings()
    app = FastAPI(
        title="Self-Claw",
        description="轻量级企业 Agent 框架",
        version=settings.app_version,
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
