"""缺陷注入实验（实施计划 P5-4；规范 §16 红线）。

人为制造 Python 项目中的 lint / test / type 缺陷，
然后跑 EndToEndPipeline 验证：

- 缺陷被真实检出（五链中的对应检查 FAILED）；
- PipelineResult.ok 变为 False；
- 不谎报为 PASSED（FAILED/UNVERIFIED 绝不被改写）。

注入采用就地修改源码的方式，恢复时用 GitWorkspace 还原——
确保恢复路径与真实开发场景一致。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from core.quality.checks import CheckKind, TestStatus


class FaultType(StrEnum):
    LINT = "lint"  # 注入 ruff 违规
    TEST = "test"  # 注入 pytest 失败
    TYPE = "type"  # 注入 mypy 类型错误


@dataclass
class InjectedFault:
    kind: FaultType
    path: Path
    description: str


class FaultInjector:
    """缺陷注入器：就地修改 Python 项目源码，事后可恢复。"""

    def __init__(self, project_root: Path | str) -> None:
        self._root = Path(project_root)
        self._injecteds: list[InjectedFault] = []

    # ---- 注入 ----

    def inject_unused_import(self, file_rel: str) -> InjectedFault:
        """在 .py 文件顶部注入 `import os`，触发 ruff F401。"""
        path = self._root / file_rel
        original = path.read_text(encoding="utf-8")
        if "import os\n" not in original:
            patched = re.sub(
                r"(?m)^from __future__ import annotations\n",
                r"from __future__ import annotations\nimport os  # P5-4 缺陷注入\n",
                original,
                count=1,
            )
            if patched == original:
                # 没有 from __future__，直接插到第一行
                patched = "import os  # P5-4 缺陷注入\n" + original
            path.write_text(patched, encoding="utf-8")
        fault = InjectedFault(FaultType.LINT, path, f"注入未使用 import os 到 {file_rel}")
        self._injecteds.append(fault)
        return fault

    def inject_assert_failure(self, file_rel: str) -> InjectedFault:
        """修改 pytest 测试中的断言使其恒为 False。"""
        path = self._root / file_rel
        original = path.read_text(encoding="utf-8")
        # 替换第一个 `assert x is y` 为 `assert False`
        patched = re.sub(
            r"(?m)def test_\w+\([^)]*\):\n((?:    .*\n)+)",
            lambda m: m.group(0).replace(
                m.group(1).split("\n")[0],
                m.group(1).split("\n")[0] + "\n    assert False  # P5-4 缺陷注入",
                1,
            ),
            original,
            count=1,
        )
        # 简化版：直接在第一个 test_ 函数体开头插一行 assert False
        patched = re.sub(
            r"(?m)^def (test_\w+)\([^)]*\):\n",
            r"def \1():\n    assert False  # P5-4 缺陷注入\n",
            original,
            count=1,
        )
        path.write_text(patched, encoding="utf-8")
        fault = InjectedFault(FaultType.TEST, path, f"注入恒失败断言到 {file_rel}")
        self._injecteds.append(fault)
        return fault

    # ---- 恢复 ----

    def restore(self) -> None:
        """直接把源码恢复为注入前的状态——本实验用 pytest fixture tmp_path 天然隔离，
        因此恢复策略就是删除整个临时项目目录（由调用方在 with 外处理）。
        这里保留方法供对称 API。"""
        self._injecteds.clear()

    @property
    def injected_count(self) -> int:
        return len(self._injecteds)


def verify_fault_was_caught(
    verification: list[Any], kind: CheckKind, *, must_fail: bool = True
) -> bool:
    """给定检查结果列表，判断指定 kind 是否被检出 FAILED。"""
    target = next((r for r in verification if r.kind is kind), None)
    if target is None:
        return False
    return target.status is TestStatus.FAILED if must_fail else target.status is TestStatus.PASSED
