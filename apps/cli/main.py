"""平台命令行（Phase 1）：项目生命周期管理与单任务操作。

一切状态变化经状态机/仓储/审计执行（§4 原则 2/11），命令实际可执行：
``python -m apps.cli [--runtime-dir DIR] <command>``。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from core.audit import AuditLogger
from core.domain.entities import Project, Task, new_id, utc_now
from core.domain.enums import EvidenceKind, ProjectStatus, TaskStatus
from core.orchestrator import RetryPolicy, StateStore
from core.workflow import (
    Evidence,
    GuardRejectionError,
    IllegalTransitionError,
    TaskStateMachine,
    TransitionContext,
)

PROJECT_KIND = "project"
TASK_KIND = "task"

# 项目级合法转移（任务级由 core/workflow 状态机强制）
_PROJECT_FLOW: dict[ProjectStatus, set[ProjectStatus]] = {
    ProjectStatus.CREATED: {ProjectStatus.ACTIVE, ProjectStatus.CANCELLED},
    ProjectStatus.ACTIVE: {ProjectStatus.PAUSED, ProjectStatus.CANCELLED},
    ProjectStatus.PAUSED: {ProjectStatus.ACTIVE, ProjectStatus.CANCELLED},
}


class CliError(Exception):
    """命令行操作错误（参数/状态不满足）。"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="apps.cli", description="AI 研发平台命令行（Phase 1）")
    parser.add_argument(
        "--runtime-dir",
        default=os.environ.get("AI_DEV_PLATFORM_RUNTIME", "runtime"),
        help="运行时目录（默认 runtime/，可用环境变量 AI_DEV_PLATFORM_RUNTIME 覆盖）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-project", help="创建项目")
    p.add_argument("--name", required=True, help="项目名称")
    p.add_argument("--workspace", default="", help="受控工作区路径（可选）")
    p.set_defaults(handler=_cmd_create_project)

    p = sub.add_parser("status", help="查看项目与任务状态汇总")
    p.add_argument("--project-id", required=True)
    p.set_defaults(handler=_cmd_status)

    p = sub.add_parser("pause", help="暂停项目（ACTIVE → PAUSED）")
    p.add_argument("--project-id", required=True)
    p.set_defaults(handler=_cmd_pause)

    p = sub.add_parser("resume", help="恢复项目（PAUSED → ACTIVE）")
    p.add_argument("--project-id", required=True)
    p.set_defaults(handler=_cmd_resume)

    p = sub.add_parser("cancel", help="取消项目（本地用户即授权方，须给原因）")
    p.add_argument("--project-id", required=True)
    p.add_argument("--reason", required=True, help="取消原因（必填，写入审计）")
    p.set_defaults(handler=_cmd_cancel)

    p = sub.add_parser("retry-task", help="重试 FAILED 任务或解除 BLOCKED 任务")
    p.add_argument("--task-id", required=True)
    p.add_argument("--reason", default="人工触发重试")
    p.set_defaults(handler=_cmd_retry_task)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (CliError, IllegalTransitionError, GuardRejectionError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1


# ---------- 公共辅助 ----------


def _open(args: argparse.Namespace) -> tuple[StateStore, AuditLogger]:
    runtime = Path(args.runtime_dir)
    store = StateStore(runtime / "state" / "platform.db")
    return store, AuditLogger(store, runtime / "logs")


def _load_project(store: StateStore, project_id: str) -> Project:
    data = store.load(PROJECT_KIND, project_id)
    if data is None:
        raise CliError(f"项目不存在: {project_id}")
    return Project.from_dict(data)


def _transition_project(
    store: StateStore, logger: AuditLogger, project: Project, to: ProjectStatus, action: str
) -> None:
    if to not in _PROJECT_FLOW.get(project.status, set()):
        raise CliError(f"非法项目转移: {project.status.value} → {to.value}")
    project.status = to
    project.version += 1
    project.updated_at = utc_now()
    store.save(PROJECT_KIND, project)
    logger.log(action, "user", project_id=project.id, detail={"to": to.value})


# ---------- 命令实现 ----------


def _cmd_create_project(args: argparse.Namespace) -> int:
    store, logger = _open(args)
    with store:
        project = Project(
            id=new_id("PRJ"),
            name=args.name,
            status=ProjectStatus.ACTIVE,
            workspace_path=args.workspace,
        )
        store.save(PROJECT_KIND, project)
        logger.log("project.created", "user", project_id=project.id, detail={"name": project.name})
        print(f"项目已创建: {project.id}（{project.name}），状态 ACTIVE")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    store, _ = _open(args)
    with store:
        project = _load_project(store, args.project_id)
        tasks = [
            Task.from_dict(r)
            for r in store.load_all(TASK_KIND)
            if r.get("project_id") == project.id
        ]
        counts: dict[str, int] = {}
        for task in tasks:
            counts[task.status.value] = counts.get(task.status.value, 0) + 1
        print(f"项目 {project.id}（{project.name}）状态 {project.status.value}")
        if not tasks:
            print("暂无任务")
        else:
            summary = "，".join(f"{k}={v}" for k, v in sorted(counts.items()))
            print(f"任务 {len(tasks)} 个: {summary}")
    return 0


def _cmd_pause(args: argparse.Namespace) -> int:
    store, logger = _open(args)
    with store:
        _transition_project(
            store,
            logger,
            _load_project(store, args.project_id),
            ProjectStatus.PAUSED,
            "project.paused",
        )
        print(f"项目已暂停: {args.project_id}")
    return 0


def _cmd_resume(args: argparse.Namespace) -> int:
    store, logger = _open(args)
    with store:
        _transition_project(
            store,
            logger,
            _load_project(store, args.project_id),
            ProjectStatus.ACTIVE,
            "project.resumed",
        )
        print(f"项目已恢复: {args.project_id}")
    return 0


def _cmd_cancel(args: argparse.Namespace) -> int:
    store, logger = _open(args)
    with store:
        project = _load_project(store, args.project_id)
        _transition_project(store, logger, project, ProjectStatus.CANCELLED, "project.cancelled")
        logger.log(
            "approval.decided",
            "user",
            project_id=project.id,
            detail={"decision": "CANCELLED", "reason": args.reason},
        )
        print(f"项目已取消: {args.project_id}（原因：{args.reason}）")
    return 0


def _cmd_retry_task(args: argparse.Namespace) -> int:
    store, logger = _open(args)
    with store:
        data = store.load(TASK_KIND, args.task_id)
        if data is None:
            raise CliError(f"任务不存在: {args.task_id}")
        task = Task.from_dict(data)
        machine = TaskStateMachine()
        policy = RetryPolicy.from_config()
        evidence = Evidence(kind=EvidenceKind.MANUAL, summary=args.reason, actor="user")
        if task.status is TaskStatus.FAILED:
            result = machine.transition(
                task,
                TaskStatus.REWORK,
                evidence,
                TransitionContext(max_retries=policy.max_retries_per_task, reason=args.reason),
            )
        elif task.status is TaskStatus.BLOCKED:
            result = machine.transition(
                task, TaskStatus.READY, evidence, TransitionContext(reason=args.reason)
            )
        else:
            raise CliError(f"仅 FAILED/BLOCKED 任务可重试，当前状态 {task.status.value}")
        store.save(TASK_KIND, task)
        logger.log(
            "task.retried",
            "user",
            project_id=task.project_id,
            task_id=task.id,
            detail={
                "from": result.from_status.value,
                "to": result.to_status.value,
                "reason": args.reason,
            },
        )
        print(f"任务 {task.id}: {result.from_status.value} → {result.to_status.value}")
    return 0
