"""P4-4 单元测试：外部 Skill 注册表与接入流水线。

覆盖：注册表字段校验（来源/许可证/状态/证据）、状态流转顺序、
未审查/未试用/未固定版本/许可证未知不可启用、拒绝终态、审计与持久化。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from core.audit.logger import AuditLogger
from core.orchestrator.store import StateStore
from core.skills import (
    ExternalSkill,
    ExternalSkillRegistry,
    ReviewStatus,
    SkillPipeline,
    SkillPipelineError,
    SkillRegistryError,
)


@pytest.fixture
def audit(tmp_path: Path) -> Iterator[AuditLogger]:
    store = StateStore(tmp_path / "state.db")
    logger = AuditLogger(store, log_dir=tmp_path / "logs")
    yield logger
    store.close()


@pytest.fixture
def registry(tmp_path: Path) -> ExternalSkillRegistry:
    return ExternalSkillRegistry(skills={}, path=tmp_path / "registry.yaml")


def _skill(skill_id: str = "demo-skill", **overrides: object) -> ExternalSkill:
    base: dict[str, object] = {
        "id": skill_id,
        "name": "示例技能",
        "source": "https://github.com/example/skills",
        "license": "MIT",
        "dependencies": ("python>=3.12",),
        "compatibility": "与平台 Skill 格式兼容（Markdown + frontmatter）",
    }
    base.update(overrides)
    return ExternalSkill(**base)  # type: ignore[arg-type]


def _write(path: Path, skills: list[dict[str, object]]) -> Path:
    path.write_text(
        yaml.safe_dump({"schema_version": 1, "skills": skills}, allow_unicode=True),
        encoding="utf-8",
    )
    return path


# ---------- 注册表 ----------


def test_default_registry_loads() -> None:
    registry = ExternalSkillRegistry.from_file()
    assert registry.list() == ()
    assert registry.enabled() == ()


def test_registry_requires_all_fields(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "bad.yaml",
        [
            {
                "id": "x",
                "name": "X",
                "source": "https://github.com/x/y",
                "review_status": "REGISTERED",
            }
        ],
    )
    with pytest.raises(SkillRegistryError, match="缺少 license"):
        ExternalSkillRegistry.from_file(path)


def test_registry_rejects_invalid_status(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "bad.yaml",
        [
            {
                "id": "x",
                "name": "X",
                "source": "https://github.com/x/y",
                "license": "MIT",
                "review_status": "APPROVED",
            }
        ],
    )
    with pytest.raises(SkillRegistryError, match="非法审核状态"):
        ExternalSkillRegistry.from_file(path)


def test_registry_rejects_duplicate_ids(tmp_path: Path) -> None:
    entry = {
        "id": "dup",
        "name": "Dup",
        "source": "https://github.com/x/y",
        "license": "MIT",
    }
    path = _write(tmp_path / "dup.yaml", [entry, dict(entry)])
    with pytest.raises(SkillRegistryError, match="ID 重复"):
        ExternalSkillRegistry.from_file(path)


def test_registry_rejects_enabled_without_version_or_evidence(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "bad.yaml",
        [
            {
                "id": "x",
                "name": "X",
                "source": "https://github.com/x/y",
                "license": "MIT",
                "review_status": "ENABLED",
            }
        ],
    )
    with pytest.raises(SkillRegistryError) as exc_info:
        ExternalSkillRegistry.from_file(path)
    text = str(exc_info.value)
    assert "review_evidence" in text and "trial_evidence" in text and "version" in text


# ---------- 流水线 ----------


def test_full_pipeline_and_persistence(
    registry: ExternalSkillRegistry, audit: AuditLogger, tmp_path: Path
) -> None:
    pipeline = SkillPipeline(registry, audit=audit)
    pipeline.register(_skill())
    assert registry.get("demo-skill").review_status is ReviewStatus.REGISTERED

    pipeline.mark_reviewed("demo-skill", "docs/reviews/demo.md")
    pipeline.sandbox_trial("demo-skill", "runtime/artifacts/trial.json")
    pipeline.pin_version("demo-skill", "a1b2c3d4")
    enabled = pipeline.enable("demo-skill")

    assert enabled.review_status is ReviewStatus.ENABLED
    assert enabled.usable is True
    assert registry.enabled() == (enabled,)

    loaded = ExternalSkillRegistry.from_file(tmp_path / "registry.yaml")
    persisted = loaded.get("demo-skill")
    assert persisted.review_status is ReviewStatus.ENABLED
    assert persisted.version == "a1b2c3d4"
    assert persisted.review_evidence == "docs/reviews/demo.md"

    actions = [e.action for e in audit.list_events()]
    assert actions == [
        "skill.registered",
        "skill.reviewed",
        "skill.sandbox_trial",
        "skill.pinned",
        "skill.enabled",
    ]
    assert audit.list_events()[-1].detail["usable"] is True


def test_register_duplicate_rejected(registry: ExternalSkillRegistry) -> None:
    pipeline = SkillPipeline(registry)
    pipeline.register(_skill())
    with pytest.raises(SkillPipelineError, match="已登记"):
        pipeline.register(_skill())


def test_cannot_skip_review(registry: ExternalSkillRegistry) -> None:
    pipeline = SkillPipeline(registry)
    pipeline.register(_skill())
    with pytest.raises(SkillPipelineError, match="非法流转"):
        pipeline.sandbox_trial("demo-skill", "evidence.json")


def test_cannot_enable_before_pin(registry: ExternalSkillRegistry) -> None:
    pipeline = SkillPipeline(registry)
    pipeline.register(_skill())
    pipeline.mark_reviewed("demo-skill", "review.md")
    pipeline.sandbox_trial("demo-skill", "trial.json")
    with pytest.raises(SkillPipelineError, match="非法流转"):
        pipeline.enable("demo-skill")


def test_review_requires_evidence(registry: ExternalSkillRegistry) -> None:
    pipeline = SkillPipeline(registry)
    pipeline.register(_skill())
    with pytest.raises(SkillPipelineError, match="证据"):
        pipeline.mark_reviewed("demo-skill", "  ")


def test_unknown_license_cannot_pin(registry: ExternalSkillRegistry) -> None:
    pipeline = SkillPipeline(registry)
    pipeline.register(_skill(license="UNKNOWN"))
    pipeline.mark_reviewed("demo-skill", "review.md")
    pipeline.sandbox_trial("demo-skill", "trial.json")
    with pytest.raises(SkillPipelineError, match="许可证未知"):
        pipeline.pin_version("demo-skill", "v1.0.0")


def test_rejected_is_terminal(registry: ExternalSkillRegistry, audit: AuditLogger) -> None:
    pipeline = SkillPipeline(registry, audit=audit)
    pipeline.register(_skill())
    rejected = pipeline.reject("demo-skill", "许可证与平台不兼容")
    assert rejected.review_status is ReviewStatus.REJECTED
    with pytest.raises(SkillPipelineError, match="非法流转"):
        pipeline.mark_reviewed("demo-skill", "review.md")
    assert audit.list_events()[-1].action == "skill.rejected"


def test_unknown_skill_raises(registry: ExternalSkillRegistry) -> None:
    pipeline = SkillPipeline(registry)
    with pytest.raises(KeyError, match="未登记"):
        pipeline.mark_reviewed("nope", "review.md")


def test_register_forces_registered_status(registry: ExternalSkillRegistry) -> None:
    pipeline = SkillPipeline(registry)
    entry = pipeline.register(_skill(review_status=ReviewStatus.ENABLED))
    assert entry.review_status is ReviewStatus.REGISTERED
