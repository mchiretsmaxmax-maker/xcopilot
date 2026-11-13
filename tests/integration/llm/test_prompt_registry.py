"""Реестр промптов ↔ таблица prompts: версия по хэшу содержимого, старые версии остаются."""

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from xcopilot.llm.prompts.registry import PromptRegistry

V1 = "---\nkey: cheap.summary\nrole: cheap\n---\nСожми: {{ text }}\n"
V2 = "---\nkey: cheap.summary\nrole: cheap\n---\nСожми в одну строку: {{ text }}\n"


async def versions(session: AsyncSession) -> list[tuple[int, str, str]]:
    rows = await session.execute(
        text(
            "SELECT version, template, content_hash FROM prompts"
            " WHERE key = 'cheap.summary' ORDER BY version"
        )
    )
    return [tuple(r) for r in rows.all()]


async def test_prompt_version_by_content_hash(session: AsyncSession, tmp_path: Path) -> None:
    path = tmp_path / "cheap.summary.md"
    path.write_text(V1)
    reg = PromptRegistry(tmp_path)
    first = (await reg.sync(session))["cheap.summary"]
    assert first.version == 1 and first.id is not None
    again = (await PromptRegistry(tmp_path).sync(session))["cheap.summary"]
    assert (again.version, again.id) == (1, first.id)  # без правок — без новой версии

    path.write_text(V2)
    reg2 = PromptRegistry(tmp_path)
    second = (await reg2.sync(session))["cheap.summary"]
    assert second.version == 2 and second.id != first.id
    rows = await versions(session)
    assert [(v, t) for v, t, _ in rows] == [
        (1, "Сожми: {{ text }}\n"),
        (2, "Сожми в одну строку: {{ text }}\n"),
    ]
    assert rows[0][2] == first.content_hash != second.content_hash

    path.write_text(V1)  # откат правки: возвращается старая версия, а не третья
    back = (await PromptRegistry(tmp_path).sync(session))["cheap.summary"]
    assert (back.version, back.id) == (1, first.id)
    assert len(await versions(session)) == 2
