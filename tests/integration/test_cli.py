"""P1-8 集成测试：CLI 实际可执行（§19.1 端到端的最小命令链）。

所有命令走真实 StateStore/状态机/审计；另含 ``python -m apps.cli`` 子进程冒烟。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from apps.cli import main
from core.domain import Task, TaskStatus
from core.orchestrator import StateStore

REPO_ROOT = Path(__file__).resolve().parents[2]


def cli(runtime: Path, *args: str) -> int:
    return main(["--runtime-dir", str(runtime), *args])


def create(runtime: Path, capsys: pytest.CaptureFixture[str], name: str = "演示项目") -> str:
    assert cli(runtime, "create-project", "--name", name) == 0
    out = capsys.readouterr().out
    return out.split("项目已创建: ")[1].split("（")[0]


def test_create_project_and_status(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pid = create(tmp_path, capsys)
    assert pid.startswith("PRJ-")
    assert cli(tmp_path, "status", "--project-id", pid) == 0
    out = capsys.readouterr().out
    assert "状态 ACTIVE" in out and "暂无任务" in out

    # 审计已记录 project.created
    with StateStore(tmp_path / "state" / "platform.db") as store:
        events = store.load_all("audit_event")
    assert any(e["action"] == "project.created" for e in events)


def test_pause_resume_cancel_flow(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pid = create(tmp_path, capsys)
    assert cli(tmp_path, "pause", "--project-id", pid) == 0
    assert "已暂停" in capsys.readouterr().out
    assert cli(tmp_path, "pause", "--project-id", pid) == 1  # PAUSED 不可再暂停
    assert cli(tmp_path, "resume", "--project-id", pid) == 0
    assert cli(tmp_path, "cancel", "--project-id", pid, "--reason", "需求取消") == 0
    assert cli(tmp_path, "resume", "--project-id", pid) == 1  # 终态不可恢复
    assert "错误" in capsys.readouterr().err


def test_cancel_requires_reason(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pid = create(tmp_path, capsys)
    with pytest.raises(SystemExit) as exc_info:
        cli(tmp_path, "cancel", "--project-id", pid)  # argparse 缺必填参数
    assert exc_info.value.code == 2


def seed_task(
    runtime: Path, project_id: str, task_id: str, status: TaskStatus, **kw: object
) -> None:
    with StateStore(runtime / "state" / "platform.db") as store:
        store.save(
            "task",
            Task(
                id=task_id,
                project_id=project_id,
                title="种子任务",
                owner_role="backend",
                status=status,
                **kw,  # type: ignore[arg-type]
            ),
        )


def load_task(runtime: Path, task_id: str) -> dict:
    with StateStore(runtime / "state" / "platform.db") as store:
        data = store.load("task", task_id)
    assert data is not None
    return json.loads(json.dumps(data))


def test_retry_failed_task_via_state_machine(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pid = create(tmp_path, capsys)
    seed_task(tmp_path, pid, "TASK-f1", TaskStatus.FAILED)

    assert cli(tmp_path, "retry-task", "--task-id", "TASK-f1") == 0
    assert "FAILED → REWORK" in capsys.readouterr().out
    task = load_task(tmp_path, "TASK-f1")
    assert task["status"] == "REWORK" and task["retry_count"] == 1

    # REWORK 状态不可经 retry-task 操作
    assert cli(tmp_path, "retry-task", "--task-id", "TASK-f1") == 1


def test_retry_blocked_task_clears_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pid = create(tmp_path, capsys)
    seed_task(tmp_path, pid, "TASK-b1", TaskStatus.BLOCKED, blocked_reason="权限不足")
    assert cli(tmp_path, "retry-task", "--task-id", "TASK-b1", "--reason", "已授权") == 0
    task = load_task(tmp_path, "TASK-b1")
    assert task["status"] == "READY" and task["blocked_reason"] is None


def test_retry_task_retry_limit_enforced(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pid = create(tmp_path, capsys)
    seed_task(tmp_path, pid, "TASK-f3", TaskStatus.FAILED, retry_count=3)
    assert cli(tmp_path, "retry-task", "--task-id", "TASK-f3") == 1  # 超限拒绝（§9.3）
    assert "上限" in capsys.readouterr().err


def test_retry_unknown_task(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli(tmp_path, "retry-task", "--task-id", "TASK-X") == 1
    assert "不存在" in capsys.readouterr().err


def test_module_executable_smoke() -> None:
    """子进程冒烟：python -m apps.cli 可执行。"""
    proc = subprocess.run(
        [sys.executable, "-m", "apps.cli", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0
    assert "create-project" in proc.stdout
