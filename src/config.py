"""应用配置与环境变量解析。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

APP_VERSION = "1.0.1"


@dataclass(frozen=True, slots=True)
class AppSettings:
    """集中管理运行期可配置参数。"""

    app_version: str = APP_VERSION
    database_path: str = "data/self_claw.db"
    memory_data_dir: str = "data/memory"
    file_sandbox_root: str = "data/workspace"
    skill_root: str = ".agents/skills"
    session_timeout_minutes: int = 30
    max_parallel_main_runs: int = 3
    max_parallel_sub_agents: int = 5
    file_lock_timeout_sec: int = 30
    file_read_max_bytes: int = 100_000
    file_write_max_bytes: int = 100_000

    @property
    def sandbox_root_path(self) -> Path:
        return Path(self.file_sandbox_root).expanduser().resolve()

    @property
    def skill_root_path(self) -> Path:
        return Path(self.skill_root).expanduser().resolve()


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """读取环境变量并返回缓存后的配置对象。"""

    return AppSettings(
        database_path=os.environ.get("DATABASE_PATH", "data/self_claw.db"),
        memory_data_dir=os.environ.get("MEMORY_DATA_DIR", "data/memory"),
        file_sandbox_root=os.environ.get("FILE_SANDBOX_ROOT", "data/workspace"),
        skill_root=os.environ.get("SKILL_ROOT", ".agents/skills"),
        session_timeout_minutes=_read_int_env("SESSION_TIMEOUT_MINUTES", default=30),
        max_parallel_main_runs=_read_int_env("MAX_PARALLEL_MAIN_RUNS", default=3),
        max_parallel_sub_agents=_read_int_env("MAX_PARALLEL_SUB_AGENTS", default=5),
        file_lock_timeout_sec=_read_int_env("FILE_LOCK_TIMEOUT_SEC", default=30),
        file_read_max_bytes=_read_int_env("FILE_READ_MAX_BYTES", default=100_000),
        file_write_max_bytes=_read_int_env("FILE_WRITE_MAX_BYTES", default=100_000),
    )


def _read_int_env(name: str, *, default: int, minimum: int = 1) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(value, minimum)