"""任务级 Git 隔离（P4-3）：分支/工作树、小提交、diff 采集与改动范围校验。"""

from core.workspace.git import (
    ChangedFile,
    DiffReport,
    GitError,
    GitUnavailable,
    GitWorkspace,
    ScopeViolation,
    TaskWorktree,
    git_executable,
)

__all__ = [
    "ChangedFile",
    "DiffReport",
    "GitError",
    "GitUnavailable",
    "GitWorkspace",
    "ScopeViolation",
    "TaskWorktree",
    "git_executable",
]
