"""ORM-модели по доменам. Импортировать можно только из пакета xcopilot.db."""

from xcopilot.db.models.access import Account, AccountMember, Secret, User
from xcopilot.db.models.base import Base
from xcopilot.db.models.content import (
    ALLOWED_TRANSITIONS,
    Draft,
    DraftStatus,
    GenerationRun,
    Publication,
    Review,
    Topic,
    assert_transition,
)
from xcopilot.db.models.derived import Fact, Insight, Lesson, StylePassport
from xcopilot.db.models.knowledge import (
    Chunk,
    Competitor,
    Document,
    Embedding,
    EmbeddingModel,
    Source,
)
from xcopilot.db.models.metrics import (
    AccountMetricSnapshot,
    Event,
    MetricDefinition,
    MetricValue,
    PostMetricSnapshot,
)
from xcopilot.db.models.ops import (
    BotSession,
    Budget,
    Job,
    LlmCall,
    Prompt,
    Schedule,
    XApiCall,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "Account",
    "AccountMember",
    "AccountMetricSnapshot",
    "Base",
    "BotSession",
    "Budget",
    "Chunk",
    "Competitor",
    "Document",
    "Draft",
    "DraftStatus",
    "Embedding",
    "EmbeddingModel",
    "Event",
    "Fact",
    "GenerationRun",
    "Insight",
    "Job",
    "Lesson",
    "LlmCall",
    "MetricDefinition",
    "MetricValue",
    "PostMetricSnapshot",
    "Prompt",
    "Publication",
    "Review",
    "Schedule",
    "Secret",
    "Source",
    "StylePassport",
    "Topic",
    "User",
    "XApiCall",
    "assert_transition",
]
