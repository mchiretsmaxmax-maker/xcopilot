"""Подделки ТЗ v3.1 для ингеста: чтение скриншота, судья проверки на входе, веб для статей."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from xcopilot.brain.schemas import Counter, ScreenshotRead
from xcopilot.ingest.net import Fetched
from xcopilot.llm.schemas import NoulA, Question, Verdict


def png(seed: int) -> bytes:
    """Уникальные байты «картинки» (содержимое не важно: читает подделка)."""
    return b"\x89PNG\r\n\x1a\n" + seed.to_bytes(8, "big") * 4


def counter(value: int | None, shown: str | None = None, confidence: float = 0.95) -> Counter:
    return Counter(value=value, as_shown=shown, confidence=confidence)


def x_read(text: str, handle: str = "alice", **fields: Any) -> ScreenshotRead:
    """Прочитанный пост X с уверенными счётчиками по умолчанию."""
    base: dict[str, Any] = {
        "is_x_post": True,
        "kind": "post",
        "author_handle": handle,
        "author_followers": counter(54_000, "54K"),
        "text": text,
        "views": counter(128_000, "128K"),
        "likes": counter(2_100, "2.1K"),
        "replies": counter(340, "340"),
        "reposts": counter(95, "95"),
        "bookmarks": counter(1_200, "1.2K"),
    }
    base.update(fields)
    return ScreenshotRead.model_validate(base)


@dataclass
class FakeScreenshotReader:
    """ScreenshotReader: ответы по байтам картинки, иначе — по очереди из `queue`."""

    by_image: dict[bytes, ScreenshotRead] = field(default_factory=dict)
    queue: list[ScreenshotRead] = field(default_factory=list)
    calls: list[tuple[bytes, str | None]] = field(default_factory=list)

    async def read_screenshot(
        self, image: bytes, note: str | None, *, account_id: UUID, mime: str = "image/png"
    ) -> ScreenshotRead:
        self.calls.append((image, note))
        if image in self.by_image:
            return self.by_image[image]
        if not self.queue:
            raise AssertionError("FakeScreenshotReader: нет ответа для картинки")
        return self.queue.pop(0)


@dataclass
class FakeDecider:
    """Судья для проверки на входе: вероятность «да» по имени вопроса (по умолчанию — допуск)."""

    p: dict[str, float] = field(
        default_factory=lambda: {"instructions_for_ai": 0.02, "is_content": 0.97, "risky": 0.02}
    )
    calls: list[str] = field(default_factory=list)

    async def decide(
        self,
        *,
        state: str,
        questions: Mapping[str, Question],
        account_id: UUID,
        purpose: str,
        entity: tuple[str, UUID] | None = None,
    ) -> Verdict:
        self.calls.append(state)
        answers = {name: NoulA(p=self.p.get(name, 0.0)) for name in questions}
        return Verdict(answers=dict(answers), judge="fake-judge", fallback=False)


@dataclass
class FakeWeb:
    """Загрузчик страниц для статей: url → HTML; неизвестный url — ошибка загрузки."""

    pages: dict[str, str] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    async def get(self, url: str, max_bytes: int) -> Fetched:
        from xcopilot.ingest.net import FetchFailed

        self.calls.append(url)
        if url not in self.pages:
            raise FetchFailed("страница не открылась")
        body = self.pages[url].encode()
        return Fetched(url=url, status=200, content_type="text/html; charset=utf-8", body=body)


def article_html(title: str, paragraphs: list[str]) -> str:
    """Страница статьи с меню, рекламой и подвалом вокруг основного текста."""
    body = "\n".join(f"<p>{p}</p>" for p in paragraphs)
    return f"""<!doctype html><html><head><title>{title} | Блог</title></head><body>
<nav><a href="/">Главная</a> <a href="/about">О нас</a> <a href="/subscribe">Подписка</a></nav>
<aside class="ads">Купите курс по трейдингу со скидкой 90%!</aside>
<article><h1>{title}</h1>
{body}
</article>
<footer>© 2026 Блог. Все права защищены. Политика cookie.</footer>
</body></html>"""
