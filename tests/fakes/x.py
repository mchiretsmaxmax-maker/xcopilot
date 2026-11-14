"""Подделки для X: FakeX (интерфейс XClient), бюджет, уведомления владельцу, сон."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from xcopilot.errors import BudgetExceeded
from xcopilot.x.errors import XModeError, XNotFound
from xcopilot.x.models import PostRef, XPost, XUser


@dataclass
class FakeX:
    """XClient в памяти: посты по id, свои посты (новые первыми), пользователи по хэндлу."""

    posts: dict[str, XPost] = field(default_factory=dict)
    own: list[XPost] = field(default_factory=list)
    users: dict[str, XUser] = field(default_factory=dict)
    mode: str = "owned"
    calls: list[str] = field(default_factory=list)

    def add_post(self, post: XPost, *, own: bool = False) -> None:
        self.posts[post.id] = post
        if own:
            self.own.insert(0, post)

    async def get_post(self, ref: PostRef) -> XPost:
        self.calls.append(f"get_post:{ref.id}")
        try:
            return self.posts[ref.id]
        except KeyError:
            raise XNotFound(f"пост {ref.id} не найден") from None

    async def get_own_posts(self, since_id: str | None = None, limit: int = 100) -> list[XPost]:
        self.calls.append(f"get_own_posts:{since_id}:{limit}")
        if self.mode == "off":
            raise XModeError("режим off")
        out = [p for p in self.own if since_id is None or int(p.id) > int(since_id)]
        return out[:limit]

    async def get_user(self, handle: str) -> XUser:
        self.calls.append(f"get_user:{handle}")
        if self.mode != "paid":
            raise XModeError("профили только в режиме paid")
        try:
            return self.users[handle.lower()]
        except KeyError:
            raise XNotFound(f"@{handle} не найден") from None


@dataclass
class FakeBudgetGuard:
    """BudgetGuardLike: общий лимит или лимиты по scope (scope_limits/scope_spent); None — без."""

    limit: int | None = None
    spent: int = 0
    scope_limits: dict[str, int] = field(default_factory=dict)
    scope_spent: dict[str, int] = field(default_factory=dict)
    checks: list[tuple[UUID, str, int]] = field(default_factory=list)

    async def check(self, account_id: UUID, scope: str, estimate: int) -> None:
        self.checks.append((account_id, scope, estimate))
        if scope in self.scope_limits:
            if self.scope_spent.get(scope, 0) + estimate > self.scope_limits[scope]:
                raise BudgetExceeded(f"{scope}: лимит исчерпан")
            return
        if self.limit is not None and self.spent + estimate > self.limit:
            raise BudgetExceeded(f"{scope}: лимит исчерпан")


@dataclass
class FakeNotifier:
    sent: list[tuple[UUID, str]] = field(default_factory=list)

    async def notify_owner(self, account_id: UUID, text: str) -> None:
        self.sent.append((account_id, text))


@dataclass
class FakeSleep:
    """Записывает паузы вместо настоящего сна."""

    waits: list[float] = field(default_factory=list)

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)
