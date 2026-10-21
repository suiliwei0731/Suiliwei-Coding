"""环境自检脚本（P0-6）。

检测 Python/Git/Node/Docker/磁盘/网络/模型端点可达性，生成根目录 PROJECT_CONTEXT.md。
诚实原则：未安装的工具标「未安装」，无法探测的项标「未验证」，不伪造可用状态。

用法：python scripts/bootstrap/check_env.py
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "PROJECT_CONTEXT.md"

TOOLS: dict[str, list[str]] = {
    "git": ["git", "--version"],
    "node": ["node", "--version"],
    "docker": ["docker", "--version"],
}
PY_PACKAGES = ["yaml", "pytest", "ruff", "mypy"]


def probe_tool(name: str, version_cmd: list[str]) -> dict[str, str]:
    """探测单个外部工具：路径 + 版本；未安装如实标注。"""
    path = shutil.which(name)
    if path is None:
        return {"status": "未安装", "version": "—", "path": "—"}
    try:
        proc = subprocess.run(version_cmd, capture_output=True, text=True, timeout=10, check=False)
        version = (
            (proc.stdout or proc.stderr).strip().splitlines()[0]
            if (proc.stdout or proc.stderr)
            else "未知"
        )
        return {"status": "可用", "version": version, "path": path}
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "未验证（探测失败）", "version": "—", "path": path}


def probe_python() -> str:
    ver = sys.version_info
    return f"Python {ver.major}.{ver.minor}.{ver.micro}（{sys.executable}）"


def probe_disk() -> str:
    total, _used, free = shutil.disk_usage(ROOT)
    return f"总 {total / 2**30:.1f} GiB / 可用 {free / 2**30:.1f} GiB"


def probe_network(host: str = "pypi.org", port: int = 443, timeout: float = 3.0) -> str:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return f"可用（{host}:{port} 连接成功）"
    except OSError as exc:
        return f"不可用（{type(exc).__name__}）"


def probe_model_endpoint() -> str:
    """模型端点探测：未配置时必须标「未配置（未验证）」，不得臆断可用。"""
    base_url = os.environ.get("AI_DEV_PLATFORM_MODEL_BASE_URL", "").strip()
    if not base_url:
        return "未配置（未验证）— 未设置 AI_DEV_PLATFORM_MODEL_BASE_URL"
    try:
        req = urllib.request.Request(base_url, method="GET")  # noqa: S310
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
            return f"可达（HTTP {resp.status}：{base_url}）"
    except Exception as exc:  # 网络探测：任何失败都如实记录
        return f"不可达（{type(exc).__name__}：{base_url}）"


def probe_packages() -> list[tuple[str, bool]]:
    return [(name, importlib.util.find_spec(name) is not None) for name in PY_PACKAGES]


def build_report() -> str:
    lines: list[str] = [
        "# PROJECT_CONTEXT.md（环境自检报告）",
        "",
        f"> 由 `scripts/bootstrap/check_env.py` 生成于 "
        f"{datetime.now().isoformat(timespec='seconds')}",
        "> 本文件为真实环境快照；每次运行覆盖。标记「未安装/未配置/未验证」的项不代表可用。",
        "",
        "## 基础环境",
        "",
        f"- Python：{probe_python()}",
        f"- 磁盘（{ROOT.drive}）：{probe_disk()}",
        f"- 网络（外联 pypi.org:443）：{probe_network()}",
        f"- 模型端点：{probe_model_endpoint()}",
        "",
        "## 外部工具",
        "",
        "| 工具 | 状态 | 版本 | 路径 |",
        "|---|---|---|---|",
    ]
    for name, cmd in TOOLS.items():
        info = probe_tool(name, cmd)
        lines.append(f"| {name} | {info['status']} | {info['version']} | {info['path']} |")

    lines += [
        "",
        "## Python 包（平台开发依赖）",
        "",
        "| 包 | 已安装 |",
        "|---|---|",
    ]
    for name, ok in probe_packages():
        lines.append(f"| {name} | {'是' if ok else '否'} |")

    lines += [
        "",
        "## 对实施计划的影响",
        "",
        "- Git 未安装时：`git init`/首次提交标注「未验证」，安装后补执行（见 CONTRIBUTING.md）。",
        "- 模型端点未配置符合预期（Phase 1–2 用模拟适配器，Phase 4 接真实模型，见 ADR-002）。",
        "- Node/Docker 为后续阶段（P6/P5）依赖，当前缺失不阻塞 Phase 0。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    report = build_report()
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"环境自检完成，报告已生成：{REPORT_PATH}")
    for name, cmd in TOOLS.items():
        info = probe_tool(name, cmd)
        print(f"  {name}: {info['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
