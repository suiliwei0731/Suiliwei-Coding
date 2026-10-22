"""外部 Skill 注册与接入流水线（P4-4）。"""

from core.skills.external import (
    DEFAULT_EXTERNAL_REGISTRY,
    ExternalSkill,
    ExternalSkillRegistry,
    ReviewStatus,
    SkillPipeline,
    SkillPipelineError,
    SkillRegistryError,
)

__all__ = [
    "DEFAULT_EXTERNAL_REGISTRY",
    "ExternalSkill",
    "ExternalSkillRegistry",
    "ReviewStatus",
    "SkillPipeline",
    "SkillPipelineError",
    "SkillRegistryError",
]
