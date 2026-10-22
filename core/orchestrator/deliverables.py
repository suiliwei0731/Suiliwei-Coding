"""交付物生成器（实施计划 P5-3；合并规范 11 章交付包）。

在 EndToEndPipeline 的 DELIVER 阶段被调用，根据流水线验证结果
自动生成：README、测试报告、需求追踪矩阵、已知限制、.env.example。

所有产物写入 ``runtime/artifacts/<project_id>/deliverable/``，
供后续交付与归档使用。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.quality.checks import CheckKind, CheckResult, TestStatus

DEFAULT_DELIVERABLE_DIR = Path("runtime/artifacts")


@dataclass(frozen=True)
class DeliverableBundle:
    """一次交付的全部产物路径集合。"""

    project_id: str
    base_dir: Path
    files: tuple[Path, ...] = ()

    @property
    def all_exist(self) -> bool:
        return all(p.is_file() for p in self.files)


class DeliverableGenerator:
    """交付物生成器：从流水线结果生成交付清单。"""

    def __init__(self, output_root: Path | str = DEFAULT_DELIVERABLE_DIR) -> None:
        self._root = Path(output_root)

    # ---- 入口 ----

    def generate(
        self,
        *,
        project_id: str,
        verification: list[CheckResult],
        source_path: Path | str,
        project_name: str = "未命名项目",
        language: str = "python",
    ) -> DeliverableBundle:
        project_dir = self._root / project_id / "deliverable"
        project_dir.mkdir(parents=True, exist_ok=True)

        readme = self._write_readme(project_dir, project_name, project_id, language, verification)
        report = self._write_test_report(project_dir, verification)
        matrix = self._write_traceability(project_dir, project_id, verification)
        limits = self._write_known_limits(project_dir, verification)
        env = self._write_env_example(project_dir)

        files = (readme, report, matrix, limits, env)
        return DeliverableBundle(project_id=project_id, base_dir=project_dir, files=files)

    # ---- 各产物 ----

    def _write_readme(
        self,
        dir_: Path,
        project_name: str,
        project_id: str,
        language: str,
        verification: list[CheckResult],
    ) -> Path:
        ok = sum(1 for r in verification if r.status is TestStatus.PASSED)
        total = len(verification)
        lines = [
            f"# {project_name}",
            "",
            f"项目 ID: `{project_id}`  语言: {language}",
            "",
            "## 验证结果",
            "",
            f"- 检查项: {ok}/{total} 通过",
        ]
        for r in verification:
            mark = (
                "✓"
                if r.status is TestStatus.PASSED
                else ("!" if r.status is TestStatus.UNVERIFIED else "✗")
            )
            lines.append(f"- {mark} {r.kind.value}: {r.tool} {r.tool_version} → {r.status.value}")
        lines.extend(
            [
                "",
                "## 启动",
                "",
                "参见 `.env.example` 环境变量模板。",
            ]
        )
        p = dir_ / "README.md"
        p.write_text("\n".join(lines), encoding="utf-8")
        return p

    def _write_test_report(self, dir_: Path, verification: list[CheckResult]) -> Path:
        """测试报告（合并规范 11.3）。"""
        lines = ["# 测试报告", ""]
        for r in verification:
            lines.append(f"## {r.kind.value} — {r.tool} v{r.tool_version or '未知'}")
            lines.append(f"- 状态: **{r.status.value}**")
            lines.append(f"- 命令: `{r.command}`")
            if r.exit_code is not None:
                lines.append(f"- 退出码: {r.exit_code}")
            if r.tool_version:
                lines.append(f"- 工具版本: {r.tool_version}")
            if r.kind is CheckKind.TEST and r.total:
                lines.append(
                    f"- 测试计数: total={r.total} passed={r.passed} "
                    f"failed={r.failed} skipped={r.skipped}"
                )
            if r.unverified_reason:
                lines.append(f"- **未验证原因**: {r.unverified_reason}")
            if r.artifact_paths:
                for ap in r.artifact_paths:
                    lines.append(f"- 产物: `{ap}`")
            if r.summary:
                lines.append("")
                lines.append("```")
                lines.append(r.summary)
                lines.append("```")
            lines.append("")
        p = dir_ / "test-report.md"
        p.write_text("\n".join(lines), encoding="utf-8")
        return p

    def _write_traceability(
        self, dir_: Path, project_id: str, verification: list[CheckResult]
    ) -> Path:
        """追踪矩阵：需求 → 任务 → 验证结果（合并规范 11.4）。"""
        header = ["check_kind", "tool", "status", "exit_code", "unverified_reason"]
        rows: list[dict[str, Any]] = []
        for r in verification:
            rows.append(
                {
                    "check_kind": r.kind.value,
                    "tool": f"{r.tool}@{r.tool_version or '?'}",
                    "status": r.status.value,
                    "exit_code": r.exit_code,
                    "unverified_reason": r.unverified_reason,
                }
            )
        data = {"project_id": project_id, "matrix_rows": rows, "header": header}
        p = dir_ / "traceability.json"
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return p

    def _write_known_limits(self, dir_: Path, verification: list[CheckResult]) -> Path:
        """已知限制：UNVERIFIED 检查及其原因汇总（合并规范 11.5）。"""
        lines = ["# 已知限制", ""]
        unverified = [r for r in verification if r.status is TestStatus.UNVERIFIED]
        if not unverified:
            lines.append("本次流水线执行中无未验证检查。")
        else:
            lines.append(
                f"本次执行包含 **{len(unverified)}** 项未验证检查，以下逐项列出原因与恢复建议。"
            )
            for r in unverified:
                lines.append("")
                lines.append(f"## {r.kind.value} — {r.tool}")
                if r.unverified_reason:
                    lines.append(f"- **原因**: {r.unverified_reason}")
                lines.append(f"- **命令**: `{r.command}`")
        p = dir_ / "known-limits.md"
        p.write_text("\n".join(lines), encoding="utf-8")
        return p

    def _write_env_example(self, dir_: Path) -> Path:
        p = dir_ / ".env.example"
        p.write_text(
            "# 环境变量模板（合并规范 11.6）\n"
            "# 复制为 .env 后按需填写；平台将从 .env 读取，不入库/不入日志。\n"
            "\n"
            "# AI 模型（真实接入时填写；未填写时流水线回退到 SimulatedAdapter）\n"
            "MODEL_BASE_URL=\n"
            "MODEL_API_KEY=\n"
            "MODEL_NAME=\n"
            "\n"
            "# 运行时\n"
            "LOG_LEVEL=INFO\n"
            "RUNTIME_DIR=runtime\n",
            encoding="utf-8",
        )
        return p


__all__ = ["DeliverableBundle", "DeliverableGenerator"]
