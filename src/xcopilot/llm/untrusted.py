"""Обёртка недоверенного контента (посты, документы, ответы X) перед вставкой в промпт."""

import re


def wrap_untrusted(text: str, label: str) -> str:
    """Оборачивает текст как данные, а не инструкции; разделители внутри обезвреживаются."""
    safe_label = re.sub(r"[^A-Za-z0-9_.-]", "_", label)
    safe_text = re.sub(r"<(/?untrusted)", r"‹\1", text, flags=re.IGNORECASE)
    return f'<untrusted label="{safe_label}">\n{safe_text}\n</untrusted>'
