"""Python 语言适配器主类（实施计划 P5-1）。

封装模板识别、五链检查执行、模板元数据查询，是 P3-3 ``PythonCheckAdapter``
的高层入口；``PythonCheckAdapter`` 继续作为底层工具执行器使用。

设计原则：
- 适配器不生成项目文件，仅对现有项目执行检查；
- 模板识别基于文件指纹（存在 pyproject.toml + scripts entry point），
  而非硬性硬编码路径——允许后续 Phase 扩展模板集；
- 对模板执行检查时，先确保项目可导入（否则跳过 pytest/build 并返回原因）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from adapters.tools.python_checks import PythonCheckAdapter
from core.quality.checks import CheckKind, CheckResult


class TemplateKind(StrEnum):
    CLI = "cli"
    SERVICE = "service"
    UNKNOWN = "unknown"


_TEMPLATES_ROOT = Path(__file__).resolve().parents[3] / "templates" / "python"


@dataclass(frozen=True)
class TemplateInfo:
    kind: TemplateKind
    path: Path
    entry_point: str
    description: str


# 模板注册表：键 = 相对 templates/python/ 的子目录名
_TEMPLATE_REGISTRY: dict[str, dict[str, str]] = {
    "cli": {
        "entry_point": "cli-validator",
        "description": "YAML 配置校验器（含 PyYAML 依赖）",
    },
    "service": {
        "entry_point": "status-service",
        "description": "极简健康状态 HTTP API（仅标准库）",
    },
}


class PythonLanguageAdapter:
    """Python 语言适配器：模板识别 + 五链检查 + 元数据查询。"""

    def __init__(self, checks: PythonCheckAdapter | None = None) -> None:
        self._checks = checks or PythonCheckAdapter()

    # ---- 模板元数据 ----

    @property
    def template_root(self) -> Path:
        return _TEMPLATES_ROOT

    def list_templates(self) -> tuple[TemplateInfo, ...]:
        infos: list[TemplateInfo] = []
        for sub, meta in _TEMPLATE_REGISTRY.items():
            p = _TEMPLATES_ROOT / sub
            infos.append(
                TemplateInfo(
                    kind=TemplateKind(sub),
                    path=p,
                    entry_point=meta["entry_point"],
                    description=meta["description"],
                )
            )
        return tuple(infos)

    def template_info(self, kind: TemplateKind) -> TemplateInfo | None:
        if kind is TemplateKind.UNKNOWN:
            return None
        meta = _TEMPLATE_REGISTRY.get(kind.value)
        if meta is None:
            return None
        return TemplateInfo(
            kind=kind,
            path=_TEMPLATES_ROOT / kind.value,
            entry_point=meta["entry_point"],
            description=meta["description"],
        )

    # ---- 项目识别 ----

    def discover_template(self, path: str | Path) -> TemplateKind:
        """识别给定目录是否为某个已知 Python 模板项目。

        识别条件：pyproject.toml 存在且 ``[project.scripts]`` 下有 entry point。
        命中则返回对应 TemplateKind，否则返回 UNKNOWN。
        """
        import tomllib

        p = Path(path)
        pyproject = p / "pyproject.toml"
        if not pyproject.is_file():
            return TemplateKind.UNKNOWN
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return TemplateKind.UNKNOWN
        scripts = (data.get("project") or {}).get("scripts") or {}
        if not scripts:
            return TemplateKind.UNKNOWN
        for sub, meta in _TEMPLATE_REGISTRY.items():
            if meta["entry_point"] in scripts:
                return TemplateKind(sub)
        return TemplateKind.UNKNOWN

    # ---- 五链检查 ----

    def run_checks(self, path: str | Path) -> list[CheckResult]:
        """对给定项目目录顺序执行 format/lint/type/test/build。"""
        path = Path(path)
        kinds = [
            CheckKind.FORMAT,
            CheckKind.LINT,
            CheckKind.TYPE_CHECK,
            CheckKind.TEST,
            CheckKind.BUILD,
        ]
        return [self._checks.run(kind, path) for kind in kinds]


def get_adapter() -> PythonLanguageAdapter:
    """模块级便捷入口（给 P5-2 端到端流水线调用）。"""
    return PythonLanguageAdapter()


__all__ = [
    "TemplateKind",
    "TemplateInfo",
    "PythonLanguageAdapter",
    "get_adapter",
]


def _serialize_for_json(result: CheckResult) -> dict[str, Any]:
    return {
        "kind": result.kind.value,
        "status": result.status.value,
        "tool": result.tool,
        "tool_version": result.tool_version,
        "exit_code": result.exit_code,
        "command": result.command,
        "summary": result.summary,
        "unverified_reason": result.unverified_reason,
        "artifact_paths": list(result.artifact_paths),
        "total": result.total,
        "passed": result.passed,
        "failed": result.failed,
        "skipped": result.skipped,
    }
