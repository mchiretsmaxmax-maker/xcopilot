"""Реестр промптов: разбор файла, рендер, кэшируемые блоки."""

from pathlib import Path

import pytest

from xcopilot.llm.errors import PromptNotFound, PromptVariableMissing
from xcopilot.llm.prompts.registry import PromptRegistry
from xcopilot.llm.roles import Role

TEMPLATE = """---
key: writer.test
role: writer
cacheable: passport, lessons
---
Ты пишешь посты для {{ handle }}.
@@user
Тема: {{topic}}
"""


def make_registry(
    tmp_path: Path, text: str = TEMPLATE, name: str = "writer.test.md"
) -> PromptRegistry:
    (tmp_path / name).write_text(text)
    return PromptRegistry(tmp_path)


def test_prompt_render_fails_on_missing_variable(tmp_path: Path) -> None:
    reg = make_registry(tmp_path)
    with pytest.raises(PromptVariableMissing) as exc:
        reg.render("writer.test", {"handle": "acc", "passport": "p", "lessons": "l"})
    assert "topic" in str(exc.value)
    with pytest.raises(PromptVariableMissing) as exc:
        reg.render("writer.test", {"handle": "acc", "topic": "t", "passport": "p"})
    assert "lessons" in str(exc.value)
    with pytest.raises(PromptNotFound):
        reg.render("nope", {})


def test_prompt_render_builds_system_user_and_cacheable_blocks(tmp_path: Path) -> None:
    reg = make_registry(tmp_path)
    version = reg.get("writer.test")
    assert version.role is Role.WRITER
    assert version.cacheable == ("passport", "lessons")
    assert len(version.content_hash) == 64
    rendered = reg.render(
        "writer.test",
        {
            "handle": "acc",
            "topic": "{{ не подставлять }}",
            "passport": "ПАСПОРТ",
            "lessons": "УРОКИ",
        },
    )
    assert rendered.user == "Тема: {{ не подставлять }}"  # значения не рендерятся повторно
    texts = [b.text for b in rendered.system]
    assert texts[0] == "Ты пишешь посты для acc."
    assert "ПАСПОРТ" in texts[1] and texts[1].startswith("<passport>")
    assert "УРОКИ" in texts[2] and texts[2].startswith("<lessons>")
    assert [b.cacheable for b in rendered.system] == [True, True, True]
    assert rendered.content_hash == version.content_hash


def test_prompt_without_cacheable_and_user_marker(tmp_path: Path) -> None:
    reg = make_registry(
        tmp_path, "---\nkey: cheap.tags\nrole: cheap\n---\nТеги: {{text}}\n", "t.md"
    )
    rendered = reg.render("cheap.tags", {"text": "пост"})
    assert rendered.system == ()
    assert rendered.user == "Теги: пост"


def test_packaged_prompts_load() -> None:
    reg = PromptRegistry()
    keys = reg.keys()
    assert "judge.fallback" in keys
    assert reg.get("judge.fallback").role is Role.CHEAP
