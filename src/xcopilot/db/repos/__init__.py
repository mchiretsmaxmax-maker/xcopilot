"""Репозитории: единственное место с SQL и моделями."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from xcopilot.db.repos.base import TenantRepo


class AccountsRepo:
    def __init__(self, session: AsyncSession) -> None:
        raise NotImplementedError

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError


class UsersRepo(AccountsRepo):
    pass


class SecretsRepo(TenantRepo[Any]):
    pass


class SourcesRepo(TenantRepo[Any]):
    pass


class ChunksRepo(TenantRepo[Any]):
    pass


class PassportsRepo(TenantRepo[Any]):
    pass


class LessonsRepo(TenantRepo[Any]):
    pass


class FactsRepo(TenantRepo[Any]):
    pass


class TopicsRepo(TenantRepo[Any]):
    pass


class RunsRepo(TenantRepo[Any]):
    pass


class DraftsRepo(TenantRepo[Any]):
    pass


class ReviewsRepo(TenantRepo[Any]):
    pass


class PublicationsRepo(TenantRepo[Any]):
    pass


class BudgetsRepo(TenantRepo[Any]):
    pass


__all__ = [
    "AccountsRepo",
    "BudgetsRepo",
    "ChunksRepo",
    "DraftsRepo",
    "FactsRepo",
    "LessonsRepo",
    "PassportsRepo",
    "PublicationsRepo",
    "ReviewsRepo",
    "RunsRepo",
    "SecretsRepo",
    "SourcesRepo",
    "TenantRepo",
    "TopicsRepo",
    "UsersRepo",
]
