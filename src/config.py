"""应用配置与环境变量解析 — 对应 SPEC Section 17.3。

配置分层：默认值 -> .agents/settings.d/*.json（深度合并） -> 环境变量（最高优先级）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields
from functools import lru_cache
from pathlib import Path

APP_VERSION = "1.2.0"


@dataclass(frozen=True, slots=True)
class AppSettings:
    """集中管理运行期可配置参数。"""

    app_version: str = APP_VERSION
    database_path: str = "data/self_claw.db"
    memory_data_dir: str = "data/memory"
    file_sandbox_root: str = "data/workspace"
    skill_root: str = ".agents/skills"
    principle_file: str = ".agents/principle.md"
    long_term_memory_dir: str = ".agents/memory/long-term"
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

    @property
    def principle_file_path(self) -> Path:
        return Path(self.principle_file).expanduser().resolve()

    @property
    def long_term_memory_dir_path(self) -> Path:
        return Path(self.long_term_memory_dir).expanduser().resolve()


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """读取环境变量并返回缓存后的配置对象。"""

    # 1. 默认值
    defaults = {f.name: f.default for f in fields(AppSettings)}

    # 2. .agents/settings.d/*.json 深度合并
    settings_d_overrides = _load_settings_d()

    # 3. 环境变量（最高优先级）
    env_overrides = _load_env_overrides()

    # 合并：defaults < settings.d < env
    merged = dict(defaults)
    for key, value in settings_d_overrides.items():
        if key in merged:
            merged[key] = value
    for key, value in env_overrides.items():
        if key in merged:
            merged[key] = value

    return AppSettings(**merged)


def _load_settings_d() -> dict[str, object]:
    """扫描 .agents/settings.d/*.json，按文件名字母序深度合并。"""
    settings_dir = Path(".agents/settings.d")
    if not settings_dir.exists():
        return {}

    merged: dict[str, object] = {}
    for json_file in sorted(settings_dir.glob("*.json")):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                merged = _deep_merge(merged, data)
        except (json.JSONDecodeError, OSError) as exc:
            import structlog
            structlog.get_logger().warning("settings_d_load_failed", file=json_file.name, error=str(exc))
    return merged


def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并：dict 递归合并，列表替换，标量覆盖。"""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


_ENV_MAPPING = {
    "DATABASE_PATH": ("database_path", str),
    "MEMORY_DATA_DIR": ("memory_data_dir", str),
    "FILE_SANDBOX_ROOT": ("file_sandbox_root", str),
    "SKILL_ROOT": ("skill_root", str),
    "PRINCIPLE_FILE": ("principle_file", str),
    "LONG_TERM_MEMORY_DIR": ("long_term_memory_dir", str),
    "SESSION_TIMEOUT_MINUTES": ("session_timeout_minutes", int),
    "MAX_PARALLEL_MAIN_RUNS": ("max_parallel_main_runs", int),
    "MAX_PARALLEL_SUB_AGENTS": ("max_parallel_sub_agents", int),
    "FILE_LOCK_TIMEOUT_SEC": ("file_lock_timeout_sec", int),
    "FILE_READ_MAX_BYTES": ("file_read_max_bytes", int),
    "FILE_WRITE_MAX_BYTES": ("file_write_max_bytes", int),
}


def _load_env_overrides() -> dict[str, object]:
    """从环境变量加载配置覆盖。"""
    overrides: dict[str, object] = {}
    for env_key, (field_name, type_) in _ENV_MAPPING.items():
        raw = os.environ.get(env_key)
        if raw is None or raw == "":
            continue
        if type_ is int:
            try:
                overrides[field_name] = max(int(raw), 1)
            except ValueError:
                continue
        else:
            overrides[field_name] = raw
    return overrides
