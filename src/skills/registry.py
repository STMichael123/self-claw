"""Agent Skills 文件注册表与激活器。"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import re
from typing import Any

from src.contracts.errors import ErrorCode

ALLOWED_FRONTMATTER_FIELDS = {
    "name",
    "description",
    "license",
    "compatibility",
    "metadata",
    "allowed-tools",
}
REQUIRED_FRONTMATTER_FIELDS = {"name", "description"}
ALLOWED_RESOURCE_DIRS = {"scripts", "references", "assets"}
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SkillRegistryError(Exception):
    """Skill 注册表错误。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(slots=True)
class ParsedSkillDocument:
    skill_name: str
    description: str
    license: str = ""
    compatibility: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)
    body: str = ""

    def frontmatter(self) -> dict[str, Any]:
        return {
            "name": self.skill_name,
            "description": self.description,
            "license": self.license,
            "compatibility": self.compatibility,
            "metadata": self.metadata,
            "allowed-tools": self.allowed_tools,
        }


@dataclass(slots=True)
class SkillCatalogEntry:
    skill_name: str
    description: str
    location: str
    compatibility: str = ""
    status: str = "enabled"
    source: str = "project"
    content_hash: str = ""
    discovered_at: str = ""
    indexed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "name": self.skill_name,
            "description": self.description,
            "location": self.location,
            "compatibility": self.compatibility,
            "status": self.status,
            "source": self.source,
            "content_hash": self.content_hash,
            "last_indexed_at": self.indexed_at,
            "indexed_at": self.indexed_at,
            "discovered_at": self.discovered_at,
        }


@dataclass(slots=True)
class ActivatedSkill:
    skill_name: str
    description: str
    compatibility: str
    allowed_tools: list[str]
    body: str
    location: str
    resource_manifest: list[dict[str, Any]] = field(default_factory=list)
    resources: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "name": self.skill_name,
            "description": self.description,
            "compatibility": self.compatibility,
            "allowed_tools": self.allowed_tools,
            "content": self.body,
            "location": self.location,
            "resource_manifest": self.resource_manifest,
            "resources": self.resources,
            "metadata": self.metadata,
        }


class SkillRegistry:
    """基于 .agents/skills 的 Agent Skills 注册表。"""

    def __init__(self, skill_root: str | Path) -> None:
        self._root = Path(skill_root).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._catalog: dict[str, SkillCatalogEntry] = {}

    @property
    def root(self) -> Path:
        return self._root

    def reload(self, *, disabled_names: set[str] | None = None) -> dict[str, Any]:
        disabled = disabled_names or set()
        discovered_count = 0
        skipped_count = 0
        invalid_count = 0
        warnings: list[str] = []
        catalog: dict[str, SkillCatalogEntry] = {}
        now = _utcnow()

        for child in sorted(self._root.iterdir(), key=lambda item: item.name.lower()):
            if not child.is_dir():
                skipped_count += 1
                continue

            skill_md = child / "SKILL.md"
            if not skill_md.exists():
                invalid_count += 1
                warnings.append(f"{child.name}: missing SKILL.md")
                continue

            try:
                header = self._read_frontmatter(skill_md)
                parsed = self._parse_frontmatter(header, expected_name=child.name)
            except SkillRegistryError as exc:
                invalid_count += 1
                warnings.append(f"{child.name}: {exc.message}")
                continue

            stat = skill_md.stat()
            content_hash = hashlib.sha256(
                f"{header}\n{stat.st_mtime_ns}\n{stat.st_size}".encode("utf-8")
            ).hexdigest()
            catalog[parsed.skill_name] = SkillCatalogEntry(
                skill_name=parsed.skill_name,
                description=parsed.description,
                compatibility=parsed.compatibility,
                location=str(child),
                status="disabled" if parsed.skill_name in disabled else "enabled",
                source="project",
                content_hash=content_hash,
                discovered_at=now,
                indexed_at=now,
            )
            discovered_count += 1

        self._catalog = catalog
        return {
            "discovered_count": discovered_count,
            "skipped_count": skipped_count,
            "invalid_count": invalid_count,
            "warnings": warnings,
        }

    def list_catalog(
        self,
        *,
        status: str | None = None,
        source: str | None = None,
        keyword: str | None = None,
    ) -> list[SkillCatalogEntry]:
        entries = list(self._catalog.values())
        if status:
            entries = [entry for entry in entries if entry.status == status]
        if source:
            entries = [entry for entry in entries if entry.source == source]
        if keyword:
            lowered = keyword.lower()
            entries = [
                entry for entry in entries
                if lowered in entry.skill_name.lower() or lowered in entry.description.lower()
            ]
        return sorted(entries, key=lambda entry: entry.skill_name)

    def get_catalog_entry(self, skill_name: str) -> SkillCatalogEntry | None:
        return self._catalog.get(skill_name)

    def get_skill_detail(self, skill_name: str) -> dict[str, Any]:
        entry = self.get_catalog_entry(skill_name)
        if entry is None:
            raise SkillRegistryError(ErrorCode.SKILL_NOT_FOUND, f"skill not found: {skill_name}")
        document = self._parse_skill_file(Path(entry.location) / "SKILL.md", expected_name=skill_name)
        return {
            **entry.to_dict(),
            "frontmatter": document.frontmatter(),
            "resource_manifest": self._build_resource_manifest(Path(entry.location)),
        }

    def activate(self, skill_name: str, *, resource_paths: list[str] | None = None) -> ActivatedSkill:
        entry = self.get_catalog_entry(skill_name)
        if entry is None:
            raise SkillRegistryError(ErrorCode.SKILL_NOT_FOUND, f"skill not found: {skill_name}")
        if entry.status != "enabled":
            raise SkillRegistryError(ErrorCode.SKILL_DISABLED, f"skill disabled: {skill_name}")

        skill_dir = Path(entry.location)
        document = self._parse_skill_file(skill_dir / "SKILL.md", expected_name=skill_name)
        manifest = self._build_resource_manifest(skill_dir)
        resources = self._load_resources(skill_dir, resource_paths or [])
        return ActivatedSkill(
            skill_name=document.skill_name,
            description=document.description,
            compatibility=document.compatibility,
            allowed_tools=document.allowed_tools,
            body=document.body,
            location=entry.location,
            resource_manifest=manifest,
            resources=resources,
            metadata=document.metadata,
        )

    def validate_skill_text(self, skill_name: str, skill_text: str) -> ParsedSkillDocument:
        return self._parse_skill_text(skill_text, expected_name=skill_name)

    def build_resource_manifest(self, skill_name: str) -> list[dict[str, Any]]:
        entry = self.get_catalog_entry(skill_name)
        if entry is None:
            raise SkillRegistryError(ErrorCode.SKILL_NOT_FOUND, f"skill not found: {skill_name}")
        return self._build_resource_manifest(Path(entry.location))

    def _parse_skill_file(self, skill_path: Path, *, expected_name: str) -> ParsedSkillDocument:
        return self._parse_skill_text(
            skill_path.read_text(encoding="utf-8", errors="replace"),
            expected_name=expected_name,
        )

    def _parse_skill_text(self, skill_text: str, *, expected_name: str) -> ParsedSkillDocument:
        header, body = _split_frontmatter(skill_text)
        document = self._parse_frontmatter(header, expected_name=expected_name)
        document.body = body.strip()
        return document

    def _parse_frontmatter(self, header: str, *, expected_name: str) -> ParsedSkillDocument:
        data = _load_yaml_like(header)
        if not isinstance(data, dict):
            raise SkillRegistryError(ErrorCode.SKILL_VALIDATION_FAILED, "frontmatter must be a mapping")

        unexpected = sorted(set(data) - ALLOWED_FRONTMATTER_FIELDS)
        if unexpected:
            raise SkillRegistryError(
                ErrorCode.SKILL_VALIDATION_FAILED,
                f"unsupported frontmatter fields: {', '.join(unexpected)}",
            )

        missing = sorted(field for field in REQUIRED_FRONTMATTER_FIELDS if not str(data.get(field, "")).strip())
        if missing:
            raise SkillRegistryError(
                ErrorCode.SKILL_VALIDATION_FAILED,
                f"missing required fields: {', '.join(missing)}",
            )

        name = str(data.get("name", "")).strip()
        if not SKILL_NAME_PATTERN.fullmatch(name):
            raise SkillRegistryError(
                ErrorCode.SKILL_VALIDATION_FAILED,
                "skill name must be lowercase kebab-case",
            )
        if name != expected_name:
            raise SkillRegistryError(
                ErrorCode.SKILL_VALIDATION_FAILED,
                f"skill name '{name}' must match directory '{expected_name}'",
            )

        metadata = data.get("metadata")
        if metadata is None or metadata == "" or metadata == "{}":
            metadata = {}
        if not isinstance(metadata, dict):
            raise SkillRegistryError(ErrorCode.SKILL_VALIDATION_FAILED, "metadata must be a mapping")

        allowed_tools_raw = data.get("allowed-tools")
        if allowed_tools_raw is None or allowed_tools_raw == "" or allowed_tools_raw == "[]":
            allowed_tools_raw = []
        if not isinstance(allowed_tools_raw, list) or any(not isinstance(item, str) for item in allowed_tools_raw):
            raise SkillRegistryError(
                ErrorCode.SKILL_VALIDATION_FAILED,
                "allowed-tools must be a string list",
            )

        return ParsedSkillDocument(
            skill_name=name,
            description=str(data.get("description", "")).strip(),
            license=str(data.get("license", "")).strip(),
            compatibility=str(data.get("compatibility", "")).strip(),
            metadata=metadata,
            allowed_tools=[item.strip() for item in allowed_tools_raw if item.strip()],
        )

    def _read_frontmatter(self, skill_path: Path) -> str:
        with skill_path.open("r", encoding="utf-8", errors="replace") as handle:
            first_line = handle.readline()
            if first_line.strip() != "---":
                raise SkillRegistryError(
                    ErrorCode.SKILL_VALIDATION_FAILED,
                    "SKILL.md must start with YAML frontmatter",
                )

            lines: list[str] = []
            for line in handle:
                if line.strip() == "---":
                    return "".join(lines)
                lines.append(line)

        raise SkillRegistryError(
            ErrorCode.SKILL_VALIDATION_FAILED,
            "SKILL.md frontmatter is not closed",
        )

    def _build_resource_manifest(self, skill_dir: Path) -> list[dict[str, Any]]:
        manifest: list[dict[str, Any]] = []
        for bucket in sorted(ALLOWED_RESOURCE_DIRS):
            base_dir = skill_dir / bucket
            if not base_dir.exists() or not base_dir.is_dir():
                continue
            for path in sorted(base_dir.rglob("*")):
                if not path.is_file():
                    continue
                manifest.append(
                    {
                        "path": path.relative_to(skill_dir).as_posix(),
                        "kind": bucket,
                        "size": path.stat().st_size,
                    }
                )
        return manifest

    def _load_resources(self, skill_dir: Path, resource_paths: list[str]) -> list[dict[str, Any]]:
        resources: list[dict[str, Any]] = []
        for raw_path in resource_paths:
            relative = self._normalize_resource_path(raw_path)
            target = (skill_dir / relative).resolve(strict=False)
            try:
                target.relative_to(skill_dir)
            except ValueError as exc:
                raise SkillRegistryError(
                    ErrorCode.SKILL_RESOURCE_ACCESS_DENIED,
                    f"resource path escapes skill root: {raw_path}",
                ) from exc

            top_level = Path(relative).parts[0] if Path(relative).parts else ""
            if top_level not in ALLOWED_RESOURCE_DIRS:
                raise SkillRegistryError(
                    ErrorCode.SKILL_RESOURCE_ACCESS_DENIED,
                    f"resource must be inside scripts/references/assets: {raw_path}",
                )
            if not target.exists() or not target.is_file():
                raise SkillRegistryError(
                    ErrorCode.SKILL_RESOURCE_ACCESS_DENIED,
                    f"resource not found: {raw_path}",
                )

            resources.append(
                {
                    "path": relative,
                    "content": target.read_text(encoding="utf-8", errors="replace"),
                }
            )
        return resources

    @staticmethod
    def _normalize_resource_path(raw_path: str) -> str:
        normalized = raw_path.strip().strip('"').strip("'")
        if not normalized:
            raise SkillRegistryError(ErrorCode.SKILL_RESOURCE_ACCESS_DENIED, "resource path is required")
        if Path(normalized).is_absolute():
            raise SkillRegistryError(ErrorCode.SKILL_RESOURCE_ACCESS_DENIED, "absolute resource path is not allowed")
        return normalized.replace("\\", "/")


def _split_frontmatter(skill_text: str) -> tuple[str, str]:
    if not skill_text.startswith("---"):
        raise SkillRegistryError(
            ErrorCode.SKILL_VALIDATION_FAILED,
            "SKILL.md must start with YAML frontmatter",
        )
    marker = "\n---\n"
    end_index = skill_text.find(marker, 4)
    if end_index < 0:
        raise SkillRegistryError(
            ErrorCode.SKILL_VALIDATION_FAILED,
            "SKILL.md frontmatter is not closed",
        )
    return skill_text[4:end_index], skill_text[end_index + len(marker):]


def _load_yaml_like(header: str) -> Any:
    try:
        import yaml  # type: ignore
    except Exception:
        yaml = None

    if yaml is not None:
        return yaml.safe_load(header) or {}

    lines = [line.rstrip("\n") for line in header.splitlines() if line.strip()]
    index = 0

    def parse_block(indent: int) -> Any:
        nonlocal index
        mapping: dict[str, Any] = {}
        items: list[Any] = []
        list_mode = False
        while index < len(lines):
            line = lines[index]
            current_indent = len(line) - len(line.lstrip(" "))
            if current_indent < indent:
                break
            if current_indent > indent:
                raise SkillRegistryError(ErrorCode.SKILL_VALIDATION_FAILED, "invalid frontmatter indentation")

            stripped = line.strip()
            if stripped.startswith("- "):
                if mapping:
                    raise SkillRegistryError(ErrorCode.SKILL_VALIDATION_FAILED, "cannot mix mapping and list at same level")
                list_mode = True
                value = stripped[2:].strip()
                index += 1
                if value:
                    items.append(_parse_scalar(value))
                else:
                    items.append(parse_block(indent + 2))
                continue

            if list_mode:
                raise SkillRegistryError(ErrorCode.SKILL_VALIDATION_FAILED, "invalid list structure")
            if ":" not in stripped:
                raise SkillRegistryError(ErrorCode.SKILL_VALIDATION_FAILED, f"invalid frontmatter line: {stripped}")

            key, raw_value = stripped.split(":", 1)
            key = key.strip()
            value = raw_value.strip()
            index += 1
            if value:
                mapping[key] = _parse_scalar(value)
                continue
            if index < len(lines):
                next_indent = len(lines[index]) - len(lines[index].lstrip(" "))
                if next_indent > indent:
                    mapping[key] = parse_block(indent + 2)
                    continue
            mapping[key] = ""
        return items if list_mode else mapping

    return parse_block(0)


def _parse_scalar(raw_value: str) -> Any:
    value = raw_value.strip()
    if not value:
        return ""
    if value.startswith(("\"", "'")) and value.endswith(("\"", "'")) and len(value) >= 2:
        return value[1:-1]
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "~"}:
        return None
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
