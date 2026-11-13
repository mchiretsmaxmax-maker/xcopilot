"""Единые типы вопросов и ответов судьи."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class NoulQ(BaseModel):
    kind: Literal["noul"] = "noul"
    instructions: str
    yes: str | None = None
    no: str | None = None


class ChoiceQ(BaseModel):
    kind: Literal["choice"] = "choice"
    instructions: str
    options: dict[str, str | None]


class ScoreQ(BaseModel):
    kind: Literal["score"] = "score"
    instructions: str
    levels: list[str]


class NoulA(BaseModel):
    kind: Literal["noul"] = "noul"
    p: float


class ChoiceA(BaseModel):
    kind: Literal["choice"] = "choice"
    choice: str
    confidence: float
    probabilities: dict[str, float]


class ScoreA(BaseModel):
    kind: Literal["score"] = "score"
    score: float
    confidence: float
    probabilities: dict[int, float]


Question = Annotated[NoulQ | ChoiceQ | ScoreQ, Field(discriminator="kind")]
Answer = Annotated[NoulA | ChoiceA | ScoreA, Field(discriminator="kind")]


class Verdict(BaseModel):
    answers: dict[str, Answer]
    judge: str
    fallback: bool = False
