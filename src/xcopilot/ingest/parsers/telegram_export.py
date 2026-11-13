"""Парсер экспорта Telegram (result.json): только сообщения владельца."""

from __future__ import annotations

import json

from xcopilot.ingest.errors import ParseError
from xcopilot.ingest.parsers.base import ParsedDoc


def parse(data: bytes, owner_tg_id: int | None = None) -> list[ParsedDoc]:
    try:
        root = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ParseError("Это не экспорт Telegram (нужен result.json).") from exc

    if not isinstance(root, dict) or not isinstance(root.get("messages"), list):
        raise ParseError("Это не экспорт Telegram (нужен result.json).")

    owner = owner_tg_id
    if owner is None:
        owner = (root.get("personal_information") or {}).get("user_id")
    if owner is None:
        raise ParseError("Не понял, где твои сообщения в экспорте.")

    texts: list[str] = []
    for item in root["messages"]:
        if item.get("type") != "message" or item.get("from_id") != f"user{owner}":
            continue

        raw_text = item.get("text")
        if isinstance(raw_text, str):
            text = raw_text
        elif isinstance(raw_text, list):
            parts: list[str] = []
            for part in raw_text:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict) and isinstance(part.get("text"), str):
                    parts.append(part["text"])
            text = "".join(parts)
        else:
            continue

        text = text.strip()
        if text:
            texts.append(text)

    if not texts:
        raise ParseError("В экспорте нет твоих сообщений.")

    return [
        ParsedDoc(text="\n\n".join(texts), meta={"messages": len(texts), "chat": root.get("name")})
    ]
