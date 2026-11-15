"""Вопросы судье для этапа 9A (ТЗ v3.1, приложение E): кандидат, голос, отношение, рубрика."""

from __future__ import annotations

from xcopilot.llm.schemas import ChoiceQ, NoulQ, ScoreQ

LAYER_OPTIONS: dict[str, str | None] = {
    "voice": "лексика, тон и манера одного автора",
    "humanness": "что делает текст живым или похожим на сгенерированный",
    "craft": "переносимый приём: хук, структура, удержание, концовка",
    "niche": "конвенция конкретной ниши и её аудитории",
    "platform": "механика площадки: длина, ссылки, медиа, время",
}

# 2. Проверка кандидата. Состояние: statement, guidance, отношения материалов-доказательств.
CANDIDATE: dict[str, NoulQ | ChoiceQ | ScoreQ] = {
    "is_craft_claim": NoulQ(
        instructions="Это утверждение о том, как писать тексты,"
        " а не о теме, человеке, продукте или рынке"
    ),
    "portable": NoulQ(instructions="Приём можно применить, не подражая манере конкретного автора"),
    "layer": ChoiceQ(instructions="К какому слою относится знание", options=LAYER_OPTIONS),
}

# 3. Совместимость с голосом. Состояние: краткий паспорт, жёсткие правила, guidance кандидата.
VOICE_COMPAT: dict[str, NoulQ | ChoiceQ | ScoreQ] = {
    "allowed": NoulQ(
        instructions="Указание можно выполнить, не нарушая ни одного жёсткого правила паспорта"
    ),
    "natural": ScoreQ(
        instructions="Насколько естественно это указание для описанного голоса",
        levels=[
            "чужеродно, ломает голос",
            "допустимо, но непривычно",
            "естественно для этого голоса",
        ],
    ),
}

# 4. Отношение кандидата к существующему знанию. Состояние: два утверждения с областями.
RELATION: dict[str, NoulQ | ChoiceQ | ScoreQ] = {
    "relation": ChoiceQ(
        instructions="Как второе утверждение относится к первому",
        options={
            "same": "говорят одно и то же другими словами",
            "narrower": "второе — частный случай первого",
            "broader": "второе обобщает первое",
            "contradicts": "при одинаковых условиях утверждают противоположное",
            "different": "о разном",
        },
    ),
}


def rubric(question: str) -> NoulQ:
    """5. Детектор-рубрика: утверждение о тексте, да или нет."""
    return NoulQ(instructions=question)
