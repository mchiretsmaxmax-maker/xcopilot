"""Карта «причина → инструкция» (этап 11, задача 7): указания писателю с цитатой места.

Ошибки линтера дают цитату найденного куска; коды судьи — указание по голосу, штампам, теме,
опоре на факты. Первая строка цитируется для слабой зацепки.
"""

from __future__ import annotations

from xcopilot.quality.policy import Decision, Reason

LINT: dict[str, str] = {
    "length": "Сократи текст: {message}.",
    "casing": "Пиши только строчными буквами.",
    "emoji": "Убери эмодзи «{fragment}».",
    "hashtags": "Убери хэштеги «{fragment}».",
    "links": "Убери ссылку «{fragment}».",
    "banned_phrases": "Убери запрещённую фразу «{fragment}».",
    "ai_patterns": "Перепиши место «{fragment}»: {message} звучит как сгенерированный текст.",
    "ai_slop": "Перепиши место «{fragment}»: AI-шаблон, {message}.",
    "number_grounding": "Убери цифру «{fragment}» или замени словами: её нет в фактах.",
    "mention_safety": "Убери упоминание «{fragment}»: его не было в задаче.",
    "duplicate_of_recent": "Текст повторяет твой недавний пост «{fragment}» — скажи по-новому.",
    "thread_structure": "Исправь структуру треда: {message}.",
    "language": "Пиши на языке задачи: {message}.",
    "foreign_similarity": "Не повторяй чужой текст «{fragment}» — перескажи своими словами.",
}
JUDGE: dict[str, str] = {
    "voice": "Голос не похож на автора: пиши ближе к его примерам — его слова, длина фраз, тон.",
    "ai_phrases": "Убери гладкость и штампы: пиши проще и конкретнее, как человек.",
    "vague": "Добавь конкретику: наблюдение, пример или деталь вместо общих слов.",
    "too_long": "Сократи: одна мысль, без вступлений и пересказа в конце.",
    "weak_hook": "Первая строка «{first_line}» не цепляет: начни с сути или наблюдения.",
    "off_topic": "Вернись к теме задачи: «{topic}».",
    "unsupported_claim": "Убери утверждения и цифры, которых нет в фактах.",
    "ai_slop": "Убери AI-шаблон «{fragment}» ({message}): скажи то же прямо, как в разговоре.",
    "risky": "Убери оскорбления, обещания доходности и призывы купить.",
}


def _line(reason: Reason, first_line: str, topic: str) -> str:
    table = LINT if reason.source == "lint" else JUDGE
    template = table.get(reason.code, "Исправь: {message} «{fragment}».")
    return template.format(
        fragment=reason.fragment,
        message=reason.message or reason.code,
        first_line=first_line,
        topic=topic,
    ).replace(" «»", "")


def to_rewrite_instructions(decision: Decision, *, topic: str = "") -> list[str]:
    """Указания писателю по причинам решения; повторы убираются, порядок сохраняется."""
    text = decision.lint.text if decision.lint is not None else ""
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")[:80]
    lines = [_line(r, first, topic) for r in decision.reasons]
    return list(dict.fromkeys(lines))
