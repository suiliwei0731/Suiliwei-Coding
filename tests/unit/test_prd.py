"""PRD 生成与模板校验测试（规范 §5.4、§8 阶段 2）。"""

from __future__ import annotations

from typing import Any

import pytest

from core.domain import (
    FactKind,
    Requirement,
    RequirementChange,
    RequirementKind,
    RequirementStatus,
    new_id,
)
from core.requirements import (
    EMPTY_SECTION,
    SECTION_TITLES,
    InterviewSession,
    PRDDocument,
    PRDSection,
    PRDSpec,
    PRDTemplateError,
    Question,
    UserStory,
    build_prd,
    save_prd,
    validate_prd_markdown,
)


def _req(**kwargs: Any) -> Requirement:
    defaults: dict[str, Any] = {
        "id": new_id("REQ"),
        "project_id": "PRJ-test000001",
        "title": "批量导入用户",
        "kind": RequirementKind.FUNCTIONAL,
        "description": "支持通过 CSV 批量导入用户账号",
        "acceptance_criteria": ["合法 CSV 全部导入成功", "非法行报告行号与原因"],
    }
    defaults.update(kwargs)
    return Requirement(**defaults)


def _story(requirement_id: str, **kwargs: Any) -> UserStory:
    defaults: dict[str, Any] = {
        "id": "US-0001",
        "requirement_id": requirement_id,
        "role": "运营人员",
        "goal": "批量导入用户账号",
        "benefit": "减少手工录入成本",
        "acceptance_criteria": ("合法 CSV 全部导入成功",),
    }
    defaults.update(kwargs)
    return UserStory(**defaults)


def _spec(**kwargs: Any) -> PRDSpec:
    defaults: dict[str, Any] = {
        "project_name": "账号中心",
        "project_idea": "统一账号管理平台",
        "generated_at": "2026-10-22T00:00:00.000Z",
    }
    defaults.update(kwargs)
    return PRDSpec(**defaults)


class TestBuildPRD:
    def test_all_sections_present_and_valid(self) -> None:
        req = _req()
        doc = build_prd(_spec(user_stories=(_story(req.id),)), [req])
        assert {s.title for s in doc.sections} == set(SECTION_TITLES)
        validate_prd_markdown(doc.render())  # 不抛异常即通过

    def test_requirements_routed_by_kind(self) -> None:
        f = _req(title="批量导入")
        nf = _req(title="导入性能", kind=RequirementKind.NON_FUNCTIONAL)
        c = _req(title="部署限制", kind=RequirementKind.CONSTRAINT)
        doc = build_prd(_spec(), [f, nf, c])
        body = {s.key: s.body for s in doc.sections}
        assert f.title in body["scope_in"] and f.id in body["scope_in"]
        assert nf.title in body["non_functional"]
        assert c.title in body["deployment"]
        # 功能范围不得混入非功能/约束需求
        assert nf.title not in body["scope_in"]
        assert c.title not in body["scope_in"]

    def test_user_story_rendered_with_criteria(self) -> None:
        req = _req()
        doc = build_prd(_spec(user_stories=(_story(req.id),)), [req])
        body = next(s.body for s in doc.sections if s.key == "user_stories")
        assert "US-0001" in body
        assert req.id in body
        assert "运营人员" in body
        assert "合法 CSV 全部导入成功" in body

    def test_user_story_unknown_requirement_rejected(self) -> None:
        req = _req()
        with pytest.raises(ValueError, match="关联的需求不存在"):
            build_prd(_spec(user_stories=(_story("REQ-404"),)), [req])

    def test_user_story_requires_acceptance_criteria(self) -> None:
        with pytest.raises(ValueError, match="缺少验收条件"):
            _story("REQ-x", acceptance_criteria=())

    def test_version_and_change_records_rendered(self) -> None:
        change = RequirementChange(
            reason="客户要求增加去重规则",
            impact_scope="导入流程与去重模块",
            cost="约 2 个任务工作量",
            affected_tasks=["TASK-aaa"],
            regression_required="导入回归测试全集",
            requested_by="user",
        )
        req = _req(
            status=RequirementStatus.APPROVED,
            version=3,
            confirmed_by="user",
            change_records=[change],
        )
        doc = build_prd(_spec(), [req])
        body = next(s.body for s in doc.sections if s.key == "version_history")
        assert "v3" in body
        assert "user" in body
        assert "增加去重规则" in body
        assert "导入回归测试全集" in body

    def test_draft_assumptions_and_open_questions_merged(self) -> None:
        session = InterviewSession(
            "账号中心",
            [
                Question(id="q1", topic="导入格式", text="支持哪种导入格式？"),
                Question(id="q2", topic="并发上限", text="并发导入上限是多少？"),
            ],
        )
        session.answer("q1", "CSV", kind=FactKind.INFERENCE, assumption=True)
        session.defer("q2", "并发指标需压测后确认")
        draft = session.draft()

        req = _req(assumptions=["CSV 编码为 UTF-8"], open_questions=["是否支持 Excel"])
        doc = build_prd(_spec(), [req], draft)
        body = next(s.body for s in doc.sections if s.key == "risks")
        assert "CSV 编码为 UTF-8" in body
        assert "是否支持 Excel" in body
        assert "导入格式: CSV" in body  # 访谈假设
        assert "并发指标需压测后确认" in body  # 访谈待确认

    def test_empty_sections_use_placeholder_not_fabricated(self) -> None:
        doc = build_prd(_spec(), [_req()])
        placeholder = [s.title for s in doc.sections if s.body == EMPTY_SECTION]
        assert "目标用户与场景" in placeholder
        assert "明确不做的内容" in placeholder
        validate_prd_markdown(doc.render())  # 占位说明仍视为有正文


class TestValidatePRDMarkdown:
    def _valid_text(self) -> str:
        return build_prd(_spec(), [_req()]).render()

    def test_missing_section_rejected(self) -> None:
        text = self._valid_text().replace("## 权限模型与审计要求", "## 权限模型", 1)
        with pytest.raises(PRDTemplateError) as exc_info:
            validate_prd_markdown(text)
        assert "权限模型与审计要求" in exc_info.value.missing

    def test_empty_section_rejected(self) -> None:
        lines = self._valid_text().splitlines()
        out: list[str] = []
        skip = False
        for line in lines:
            if line.startswith("## 异常与边界"):
                out.append(line)
                out.append("")
                skip = True  # 丢弃该章节原有正文
                continue
            if skip and line.startswith("## "):
                skip = False
            if not skip:
                out.append(line)
        with pytest.raises(PRDTemplateError) as exc_info:
            validate_prd_markdown("\n".join(out) + "\n")
        assert "异常与边界" in exc_info.value.empty

    def test_out_of_order_rejected(self) -> None:
        text = self._valid_text()
        # 交换"功能范围"与"明确不做的内容"两章节位置
        start_in = text.index("## 功能范围")
        start_out = text.index("## 明确不做的内容")
        end_out = text.index("## 用户故事与验收条件")
        reordered = (
            text[:start_in] + text[start_out:end_out] + text[start_in:start_out] + text[end_out:]
        )
        with pytest.raises(PRDTemplateError) as exc_info:
            validate_prd_markdown(reordered)
        assert exc_info.value.out_of_order


class TestSavePRD:
    def test_save_writes_valid_file(self, tmp_path: Any) -> None:
        doc = build_prd(_spec(), [_req()])
        path = save_prd(doc, tmp_path / "requirements")
        assert path.name == "PRD-v1.md"
        text = path.read_text(encoding="utf-8")
        validate_prd_markdown(text)  # 落盘内容可复验
        assert "# PRD：账号中心" in text

    def test_invalid_document_not_written(self, tmp_path: Any) -> None:
        bad = PRDDocument(
            project_name="坏文档",
            prd_version=1,
            generated_at="2026-10-22T00:00:00.000Z",
            sections=tuple(
                PRDSection(key=k, title=t, body="" if k == "scope_out" else "有内容")
                for k, t in (
                    ("goal_background", "项目目标与背景"),
                    ("scope_out", "明确不做的内容"),
                )
            ),
        )
        with pytest.raises(PRDTemplateError):
            save_prd(bad, tmp_path / "requirements")
        assert not list((tmp_path / "requirements").glob("*.md"))
