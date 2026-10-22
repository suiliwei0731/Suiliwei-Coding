"""检查点（ADR-001）：调度器状态快照。

写入采用"临时文件 + os.replace"原子替换，避免崩溃留下半截 JSON；
快照文件存 runtime/checkpoints/，数据库仅存状态，两者职责分离。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_INVALID_NAME_CHARS = set('\\/:*?"<>|')


class CheckpointManager:
    """检查点读写：按名称管理 runtime/checkpoints 下的快照文件。"""

    def __init__(self, checkpoint_dir: Path | str) -> None:
        self._dir = Path(checkpoint_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, name: str, data: dict[str, Any]) -> Path:
        target = self._path_for(name)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)  # 原子替换：崩溃不会留下半截文件
        return target

    def load(self, name: str) -> dict[str, Any] | None:
        target = self._path_for(name)
        if not target.is_file():
            return None
        return json.loads(target.read_text(encoding="utf-8"))

    def _path_for(self, name: str) -> Path:
        if not name or _INVALID_NAME_CHARS & set(name):
            raise ValueError(f"非法检查点名: {name!r}")
        return self._dir / f"{name}.checkpoint.json"
