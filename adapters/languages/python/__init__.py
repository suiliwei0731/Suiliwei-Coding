"""Python 语言适配器包。"""

from adapters.languages.python.adapter import (
    PythonLanguageAdapter,
    TemplateInfo,
    TemplateKind,
    get_adapter,
)

__all__ = [
    "TemplateInfo",
    "TemplateKind",
    "PythonLanguageAdapter",
    "get_adapter",
]
