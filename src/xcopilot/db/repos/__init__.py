"""Репозитории: единственное место с SQL запросами к данным и моделями.

Тенантные репозитории (`TenantRepo`) требуют account_id и не видят чужих строк.
`AccountsRepo` и `UsersRepo` — глобальные.
"""

from xcopilot.db.repos.accounts import AccountsRepo, UsersRepo
from xcopilot.db.repos.base import TenantRepo
from xcopilot.db.repos.bot_sessions import BotSessionsRepo
from xcopilot.db.repos.budgets import BudgetsRepo
from xcopilot.db.repos.chunks import ChunksRepo
from xcopilot.db.repos.drafts import DraftsRepo
from xcopilot.db.repos.facts import FactsRepo
from xcopilot.db.repos.lessons import LessonsRepo
from xcopilot.db.repos.metrics import EventsRepo, MetricDefsRepo, MetricValuesRepo
from xcopilot.db.repos.passports import PassportsRepo
from xcopilot.db.repos.publications import PublicationsRepo
from xcopilot.db.repos.reviews import ReviewsRepo
from xcopilot.db.repos.runs import RunsRepo
from xcopilot.db.repos.secrets import SecretsRepo
from xcopilot.db.repos.sources import SourcesRepo
from xcopilot.db.repos.topics import TopicsRepo
from xcopilot.db.repos.usage import UsageRepo

__all__ = [
    "AccountsRepo",
    "BotSessionsRepo",
    "BudgetsRepo",
    "ChunksRepo",
    "DraftsRepo",
    "EventsRepo",
    "FactsRepo",
    "LessonsRepo",
    "MetricDefsRepo",
    "MetricValuesRepo",
    "PassportsRepo",
    "PublicationsRepo",
    "ReviewsRepo",
    "RunsRepo",
    "SecretsRepo",
    "SourcesRepo",
    "TenantRepo",
    "TopicsRepo",
    "UsageRepo",
    "UsersRepo",
]
