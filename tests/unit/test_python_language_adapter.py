"""P5-1 单元测试：Python 语言适配器。"""

from __future__ import annotations

from pathlib import Path

import pytest

from adapters.languages.python import (
    PythonLanguageAdapter,
    TemplateKind,
    get_adapter,
)
from core.quality.checks import CheckKind, TestStatus


@pytest.fixture
def adapter() -> PythonLanguageAdapter:
    return PythonLanguageAdapter()


def test_list_templates_returns_two(adapter: PythonLanguageAdapter) -> None:
    infos = adapter.list_templates()
    assert len(infos) == 2
    kinds = {t.kind for t in infos}
    assert kinds == {TemplateKind.CLI, TemplateKind.SERVICE}


def test_template_info_for_each_kind(adapter: PythonLanguageAdapter) -> None:
    for kind in (TemplateKind.CLI, TemplateKind.SERVICE):
        info = adapter.template_info(kind)
        assert info is not None
        assert info.kind is kind
        assert info.path.is_dir()
        assert (info.path / "pyproject.toml").is_file()


def test_template_info_unknown(adapter: PythonLanguageAdapter) -> None:
    assert adapter.template_info(TemplateKind.UNKNOWN) is None


def test_discover_template_cli(adapter: PythonLanguageAdapter) -> None:
    info = adapter.template_info(TemplateKind.CLI)
    assert info is not None
    assert adapter.discover_template(info.path) is TemplateKind.CLI


def test_discover_template_service(adapter: PythonLanguageAdapter) -> None:
    info = adapter.template_info(TemplateKind.SERVICE)
    assert info is not None
    assert adapter.discover_template(info.path) is TemplateKind.SERVICE


def test_discover_template_unknown(tmp_path: Path, adapter: PythonLanguageAdapter) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\\nname='x'\\n", encoding="utf-8")
    assert adapter.discover_template(tmp_path) is TemplateKind.UNKNOWN
    # 无 pyproject.toml
    empty = tmp_path / "empty"
    empty.mkdir()
    assert adapter.discover_template(empty) is TemplateKind.UNKNOWN


def test_run_checks_on_cli_template(adapter: PythonLanguageAdapter) -> None:
    info = adapter.template_info(TemplateKind.CLI)
    assert info is not None
    results = adapter.run_checks(info.path)
    assert [r.kind for r in results] == [
        CheckKind.FORMAT,
        CheckKind.LINT,
        CheckKind.TYPE_CHECK,
        CheckKind.TEST,
        CheckKind.BUILD,
    ]
    for r in results:
        assert r.status in (TestStatus.PASSED, TestStatus.FAILED, TestStatus.UNVERIFIED)
        assert r.tool  # 工具名不为空


def test_get_adapter_returns_valid_instance() -> None:
    a1 = get_adapter()
    a2 = get_adapter()
    assert isinstance(a1, PythonLanguageAdapter)
    assert isinstance(a2, PythonLanguageAdapter)
    # 模块级便捷入口每次返回新实例——调用方应自行持有
    assert a1 is not a2
