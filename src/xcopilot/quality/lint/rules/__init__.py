"""Правила линтера v1 (этап 11, задача 2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xcopilot.quality.lint.engine import Rule


def all_rules() -> list[Rule]:
    from xcopilot.quality.lint.rules.ai_patterns import AiPatternsRule
    from xcopilot.quality.lint.rules.ai_slop import AiSlopRule
    from xcopilot.quality.lint.rules.banned import BannedPhrasesRule
    from xcopilot.quality.lint.rules.casing import CasingRule
    from xcopilot.quality.lint.rules.duplicate import DuplicateOfRecentRule
    from xcopilot.quality.lint.rules.emoji import EmojiRule
    from xcopilot.quality.lint.rules.foreign import ForeignSimilarityRule
    from xcopilot.quality.lint.rules.hashtags import HashtagsRule
    from xcopilot.quality.lint.rules.knowledge_c import ClassCRule
    from xcopilot.quality.lint.rules.language import LanguageRule
    from xcopilot.quality.lint.rules.length import LengthRule
    from xcopilot.quality.lint.rules.length_band import LengthBandRule
    from xcopilot.quality.lint.rules.links import LinksRule
    from xcopilot.quality.lint.rules.mentions import MentionSafetyRule
    from xcopilot.quality.lint.rules.numbers import NumberGroundingRule
    from xcopilot.quality.lint.rules.thread import ThreadStructureRule

    return [
        CasingRule(),
        HashtagsRule(),
        LengthRule(),
        LengthBandRule(),
        EmojiRule(),
        LinksRule(),
        BannedPhrasesRule(),
        AiPatternsRule(),
        AiSlopRule(),
        NumberGroundingRule(),
        MentionSafetyRule(),
        DuplicateOfRecentRule(),
        ThreadStructureRule(),
        LanguageRule(),
        ForeignSimilarityRule(),
        ClassCRule(),
    ]
