"""Единые типы вопросов и ответов судьи."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

MAX_CHOICES = 255
MAX_LEVELS = 10


class NoulQ(BaseModel):
    """Вопрос да/нет."""

    kind: Literal["noul"] = "noul"
    instructions: str = Field(min_length=1)
    yes: str | None = None
    no: str | None = None


class ChoiceQ(BaseModel):
    """Выбор из вариантов (до 255), ключ — метка, значение — описание или None."""

    kind: Literal["choice"] = "choice"
    instructions: str = Field(min_length=1)
    options: dict[str, str | None] = Field(min_length=2, max_length=MAX_CHOICES)


class ScoreQ(BaseModel):
    """Оценка по упорядоченной шкале (до 10 уровней), уровень i = levels[i]."""

    kind: Literal["score"] = "score"
    instructions: str = Field(min_length=1)
    levels: list[str] = Field(min_length=2, max_length=MAX_LEVELS)


class NoulA(BaseModel):
    """Вероятность «да»."""

    kind: Literal["noul"] = "noul"
    p: float = Field(ge=0, le=1)


class ChoiceA(BaseModel):
    """Выбранный вариант, уверенность и вероятности."""

    kind: Literal["choice"] = "choice"
    choice: str
    confidence: float = Field(ge=0, le=1)
    probabilities: dict[str, float]


class ScoreA(BaseModel):
    """Ожидаемая оценка, уверенность и вероятности по уровням."""

    kind: Literal["score"] = "score"
    score: float
    confidence: float = Field(ge=0, le=1)
    probabilities: dict[int, float]


Question = Annotated[NoulQ | ChoiceQ | ScoreQ, Field(discriminator="kind")]
Answer = Annotated[NoulA | ChoiceA | ScoreA, Field(discriminator="kind")]


class Verdict(BaseModel):
    """Ответы судьи, имя модели-судьи и флаг резервного судьи."""

    answers: dict[str, Answer]
    judge: str
    fallback: bool = False
