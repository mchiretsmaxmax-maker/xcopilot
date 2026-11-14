"""Репозитории: единственное место с SQL запросами к данным и моделями.

Тенантные репозитории (`TenantRepo`) требуют account_id и не видят чужих строк.
`AccountsRepo` и `UsersRepo` — глобальные.
"""

from xcopilot.db.repos.accounts import AccountsRepo, UsersRepo
from xcopilot.db.repos.authors import AuthorsRepo
from xcopilot.db.repos.base import TenantRepo
from xcopilot.db.repos.bot_sessions import BotSessionsRepo
from xcopilot.db.repos.budgets import BudgetsRepo
from xcopilot.db.repos.chunks import ChunksRepo, EmbeddingModelsRepo
from xcopilot.db.repos.drafts import DraftsRepo
from xcopilot.db.repos.facts import FactsRepo
from xcopilot.db.repos.knowledge import (
    KnowledgeApplicationsRepo,
    KnowledgeEmbeddingsRepo,
    KnowledgeEvidenceRepo,
    KnowledgeItemsRepo,
    KnowledgeLinksRepo,
    KnowledgePacksRepo,
    KnowledgeSnapshotsRepo,
    SegmentsRepo,
)
from xcopilot.db.repos.llm_calls import LlmCallsRepo
from xcopilot.db.repos.materials import (
    MaterialAnalysesRepo,
    MaterialOutcomesRepo,
    MaterialSignalsRepo,
    TaxonomyRepo,
)
from xcopilot.db.repos.metrics import EventsRepo, MetricDefsRepo, MetricValuesRepo
from xcopilot.db.repos.passports import PassportsRepo
from xcopilot.db.repos.prompts import PromptsRepo
from xcopilot.db.repos.publications import PublicationsRepo
from xcopilot.db.repos.retrieval import RetrievalRepo
from xcopilot.db.repos.reviews import ReviewsRepo
from xcopilot.db.repos.runs import RunsRepo
from xcopilot.db.repos.secrets import SecretsRepo
from xcopilot.db.repos.sources import SourcesRepo
from xcopilot.db.repos.spend import SpendRepo
from xcopilot.db.repos.topics import TopicsRepo
from xcopilot.db.repos.usage import UsageRepo
from xcopilot.db.repos.x_api_calls import XApiCallsRepo

__all__ = [
    "AccountsRepo",
    "AuthorsRepo",
    "BotSessionsRepo",
    "BudgetsRepo",
    "ChunksRepo",
    "DraftsRepo",
    "EmbeddingModelsRepo",
    "EventsRepo",
    "FactsRepo",
    "KnowledgeApplicationsRepo",
    "KnowledgeEmbeddingsRepo",
    "KnowledgeEvidenceRepo",
    "KnowledgeItemsRepo",
    "KnowledgeLinksRepo",
    "KnowledgePacksRepo",
    "KnowledgeSnapshotsRepo",
    "LlmCallsRepo",
    "MaterialAnalysesRepo",
    "MaterialOutcomesRepo",
    "MaterialSignalsRepo",
    "MetricDefsRepo",
    "MetricValuesRepo",
    "PassportsRepo",
    "PromptsRepo",
    "PublicationsRepo",
    "RetrievalRepo",
    "ReviewsRepo",
    "RunsRepo",
    "SecretsRepo",
    "SegmentsRepo",
    "SourcesRepo",
    "SpendRepo",
    "TaxonomyRepo",
    "TenantRepo",
    "TopicsRepo",
    "UsageRepo",
    "UsersRepo",
    "XApiCallsRepo",
]
