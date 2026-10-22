"""外部 Skill 注册表与接入流水线（实施计划 P4-4；规范 §14.2、§17）。

接入顺序（config/policies.yaml ``skills.external_pipeline``）：
``register → review → sandbox_trial → pin_version → enable``。

红线落实：
- 外部技能指令性内容不得高于平台安全策略（policies.yaml
  ``skills.external_policy_precedence: platform``）；
- 未审查、未隔离试用、未固定版本的外部技能不可启用（状态机拒绝）；
- 每条流转写审计（``skill.registered/reviewed/sandbox_trial/pinned/enabled/rejected``），
  来源、许可证、版本、审查与试用证据可追踪；
- 许可证未知（UNKNOWN/NOASSERTION/空）不得固定版本或启用，不盲目采用。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from core.audit.logger import AuditLogger

DEFAULT_EXTERNAL_REGISTRY = (
    Path(__file__).resolve().parents[2] / "knowledge" / "skills-registry" / "registry.yaml"
)
SKILL_ACTOR = "技能接入流水线"
FILE_SCHEMA_VERSION = 1
UNKNOWN_LICENSES = frozenset({"", "UNKNOWN", "NOASSERTION", "TBD"})


class ReviewStatus(StrEnum):
    """外部技能审核状态（单向流水线，REJECTED 为终态）。"""

    REGISTERED = "REGISTERED"
    REVIEWED = "REVIEWED"
    SANDBOX_TRIAL = "SANDBOX_TRIAL"
    PINNED = "PINNED"
    ENABLED = "ENABLED"
    REJECTED = "REJECTED"


_ALLOWED_TRANSITIONS: dict[ReviewStatus, frozenset[ReviewStatus]] = {
    ReviewStatus.REGISTERED: frozenset({ReviewStatus.REVIEWED, ReviewStatus.REJECTED}),
    ReviewStatus.REVIEWED: frozenset({ReviewStatus.SANDBOX_TRIAL, ReviewStatus.REJECTED}),
    ReviewStatus.SANDBOX_TRIAL: frozenset({ReviewStatus.PINNED, ReviewStatus.REJECTED}),
    ReviewStatus.PINNED: frozenset({ReviewStatus.ENABLED, ReviewStatus.REJECTED}),
    ReviewStatus.ENABLED: frozenset(),
    ReviewStatus.REJECTED: frozenset(),
}


class SkillRegistryError(ValueError):
    """注册表解析或条目校验失败（problems 一次性列出全部问题）。"""

    def __init__(self, problems: list[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("外部技能注册表未通过校验: " + "; ".join(problems))


class SkillPipelineError(RuntimeError):
    """非法流转（跳过审查/试用/固定版本，或对终态继续操作）。"""


@dataclass(frozen=True)
class ExternalSkill:
    """一条外部技能登记记录（来源/许可证/版本/审核状态/证据）。"""

    id: str
    name: str
    source: str  # 仓库或来源 URL
    license: str
    review_status: ReviewStatus = ReviewStatus.REGISTERED
    version: str = ""  # 固定版本（commit SHA / tag）；未固定为空
    review_evidence: str = ""  # 审查报告路径
    trial_evidence: str = ""  # 隔离试用证据路径
    dependencies: tuple[str, ...] = ()
    compatibility: str = ""
    notes: str = ""

    def validate(self) -> list[str]:
        """条目语义校验；返回全部问题（空列表表示合法）。"""
        problems: list[str] = []
        for key, value in (
            ("id", self.id),
            ("name", self.name),
            ("source", self.source),
            ("license", self.license),
        ):
            if not str(value).strip():
                problems.append(f"技能 {self.id or '(无 ID)'} 缺少 {key}")
        if self.source and not self.source.startswith(("http://", "https://", "git@")):
            problems.append(f"技能 {self.id} source 须为 http(s):// 或 git@ 来源")
        status = self.review_status
        if (
            status
            in {
                ReviewStatus.REVIEWED,
                ReviewStatus.SANDBOX_TRIAL,
                ReviewStatus.PINNED,
                ReviewStatus.ENABLED,
            }
            and not self.review_evidence.strip()
        ):
            problems.append(f"技能 {self.id} 状态 {status.value} 缺少 review_evidence")
        if (
            status
            in {
                ReviewStatus.SANDBOX_TRIAL,
                ReviewStatus.PINNED,
                ReviewStatus.ENABLED,
            }
            and not self.trial_evidence.strip()
        ):
            problems.append(f"技能 {self.id} 状态 {status.value} 缺少 trial_evidence")
        if status in {ReviewStatus.PINNED, ReviewStatus.ENABLED}:
            if not self.version.strip():
                problems.append(f"技能 {self.id} 状态 {status.value} 缺少固定 version")
            if self.license.strip().upper() in UNKNOWN_LICENSES:
                problems.append(f"技能 {self.id} 许可证未知，不得固定版本或启用")
        return problems

    @property
    def version_pinned(self) -> bool:
        return bool(self.version.strip())

    @property
    def usable(self) -> bool:
        """只有 ENABLED 且版本固定、许可证明确的条目可被引用。"""
        return (
            self.review_status is ReviewStatus.ENABLED
            and self.version_pinned
            and self.license.strip().upper() not in UNKNOWN_LICENSES
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "license": self.license,
            "review_status": self.review_status.value,
            "version": self.version,
            "review_evidence": self.review_evidence,
            "trial_evidence": self.trial_evidence,
            "dependencies": list(self.dependencies),
            "compatibility": self.compatibility,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExternalSkill:
        raw_status = str(data.get("review_status", ReviewStatus.REGISTERED.value))
        try:
            status = ReviewStatus(raw_status)
        except ValueError as exc:
            raise ValueError(f"非法审核状态: {raw_status!r}") from exc
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            source=str(data.get("source", "")),
            license=str(data.get("license", "")),
            review_status=status,
            version=str(data.get("version", "")),
            review_evidence=str(data.get("review_evidence", "")),
            trial_evidence=str(data.get("trial_evidence", "")),
            dependencies=tuple(str(x) for x in data.get("dependencies", ())),
            compatibility=str(data.get("compatibility", "")),
            notes=str(data.get("notes", "")),
        )


@dataclass
class ExternalSkillRegistry:
    """外部技能注册表（内存 + YAML 持久化）。"""

    skills: dict[str, ExternalSkill] = field(default_factory=dict)
    path: Path | None = None

    @classmethod
    def from_file(cls, path: Path | str = DEFAULT_EXTERNAL_REGISTRY) -> ExternalSkillRegistry:
        path = Path(path)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except OSError as exc:
            raise SkillRegistryError([f"注册表不可读: {path}（{exc}）"]) from exc
        except yaml.YAMLError as exc:
            raise SkillRegistryError([f"注册表 YAML 解析失败: {path}（{exc}）"]) from exc
        problems: list[str] = []
        if not isinstance(data, dict):
            raise SkillRegistryError(["注册表内容不是映射"])
        if data.get("schema_version") != FILE_SCHEMA_VERSION:
            problems.append(
                f"schema_version 应为 {FILE_SCHEMA_VERSION}: {data.get('schema_version')!r}"
            )
        raw_skills = data.get("skills")
        if not isinstance(raw_skills, list):
            problems.append("注册表缺少 skills 列表")
            raw_skills = []
        skills: dict[str, ExternalSkill] = {}
        for raw in raw_skills:
            if not isinstance(raw, dict):
                problems.append(f"技能条目不是映射: {raw!r}")
                continue
            try:
                skill = ExternalSkill.from_dict(raw)
            except ValueError as exc:
                problems.append(str(exc))
                continue
            problems.extend(skill.validate())
            if skill.id in skills:
                problems.append(f"技能 ID 重复: {skill.id}")
                continue
            skills[skill.id] = skill
        if problems:
            raise SkillRegistryError(problems)
        return cls(skills=skills, path=path)

    def get(self, skill_id: str) -> ExternalSkill:
        try:
            return self.skills[skill_id]
        except KeyError:
            raise KeyError(f"未登记的外部技能: {skill_id!r}") from None

    def list(self) -> tuple[ExternalSkill, ...]:
        return tuple(self.skills[k] for k in sorted(self.skills))

    def enabled(self) -> tuple[ExternalSkill, ...]:
        return tuple(s for s in self.list() if s.usable)

    def put(self, skill: ExternalSkill) -> None:
        self.skills[skill.id] = skill

    def save(self, path: Path | str | None = None) -> Path:
        """原子写入 YAML（同目录临时文件 + replace）；未指定路径时用加载路径。"""
        target = Path(path) if path is not None else self.path
        if target is None:
            raise SkillRegistryError(["未指定注册表保存路径"])
        payload = {
            "schema_version": FILE_SCHEMA_VERSION,
            "skills": [s.to_dict() for s in self.list()],
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        tmp.replace(target)
        self.path = target
        return target


class SkillPipeline:
    """外部技能接入流水线：登记 → 审查 → 隔离试用 → 固定版本 → 启用。"""

    def __init__(
        self,
        registry: ExternalSkillRegistry,
        *,
        audit: AuditLogger | None = None,
        save: bool = True,
    ) -> None:
        self._registry = registry
        self._audit = audit
        self._save = save

    def register(self, skill: ExternalSkill) -> ExternalSkill:
        """登记外部技能（强制初始状态 REGISTERED）；重复 ID 拒绝。"""
        if skill.id in self._registry.skills:
            raise SkillPipelineError(f"技能已登记: {skill.id}")
        entry = replace(skill, review_status=ReviewStatus.REGISTERED)
        problems = entry.validate()
        if problems:
            raise SkillRegistryError(problems)
        self._registry.put(entry)
        self._persist()
        self._log("skill.registered", entry, {"source": entry.source, "license": entry.license})
        return entry

    def mark_reviewed(self, skill_id: str, evidence: str) -> ExternalSkill:
        if not evidence.strip():
            raise SkillPipelineError("审查结论必须附证据路径（review_evidence）")
        return self._advance(
            skill_id,
            ReviewStatus.REVIEWED,
            review_evidence=evidence,
            action="skill.reviewed",
        )

    def sandbox_trial(self, skill_id: str, evidence: str) -> ExternalSkill:
        if not evidence.strip():
            raise SkillPipelineError("隔离试用必须附证据路径（trial_evidence）")
        return self._advance(
            skill_id,
            ReviewStatus.SANDBOX_TRIAL,
            trial_evidence=evidence,
            action="skill.sandbox_trial",
        )

    def pin_version(self, skill_id: str, version: str) -> ExternalSkill:
        """固定版本；许可证未知或未试用时拒绝（不盲目采用）。"""
        if not version.strip():
            raise SkillPipelineError("固定版本不得为空（须 commit SHA 或 tag）")
        skill = self._registry.get(skill_id)
        if skill.license.strip().upper() in UNKNOWN_LICENSES:
            raise SkillPipelineError(f"许可证未知（{skill.license!r}），不得固定版本")
        return self._advance(
            skill_id,
            ReviewStatus.PINNED,
            version=version.strip(),
            action="skill.pinned",
        )

    def enable(self, skill_id: str) -> ExternalSkill:
        """启用；仅 PINNED 且证据/版本/许可证齐全时允许。"""
        return self._advance(skill_id, ReviewStatus.ENABLED, action="skill.enabled")

    def reject(self, skill_id: str, reason: str) -> ExternalSkill:
        if not reason.strip():
            raise SkillPipelineError("拒绝须记录理由（不盲目采用）")
        return self._advance(
            skill_id,
            ReviewStatus.REJECTED,
            notes=reason,
            action="skill.rejected",
        )

    def _advance(
        self,
        skill_id: str,
        to_status: ReviewStatus,
        *,
        action: str,
        **changes: Any,
    ) -> ExternalSkill:
        skill = self._registry.get(skill_id)
        allowed = _ALLOWED_TRANSITIONS[skill.review_status]
        if to_status not in allowed:
            raise SkillPipelineError(
                f"非法流转: {skill.review_status.value} → {to_status.value}（外部技能须按 "
                "register→review→sandbox_trial→pin_version→enable 顺序接入）"
            )
        updated = replace(skill, review_status=to_status, **changes)
        problems = updated.validate()
        if problems:
            raise SkillRegistryError(problems)
        self._registry.put(updated)
        self._persist()
        self._log(
            action,
            updated,
            {
                "from": skill.review_status.value,
                "to": to_status.value,
                "version": updated.version,
                "license": updated.license,
                "review_evidence": updated.review_evidence,
                "trial_evidence": updated.trial_evidence,
            },
        )
        return updated

    def _persist(self) -> None:
        if self._save and self._registry.path is not None:
            self._registry.save()

    def _log(self, action: str, skill: ExternalSkill, extra: dict[str, Any]) -> None:
        if self._audit is None:
            return
        detail = {
            "skill_id": skill.id,
            "source": skill.source,
            "review_status": skill.review_status.value,
            "version": skill.version,
            "license": skill.license,
            "usable": skill.usable,
            **extra,
        }
        self._audit.log(action, SKILL_ACTOR, detail=detail)
