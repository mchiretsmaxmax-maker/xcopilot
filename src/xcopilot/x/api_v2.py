"""Клиент X API v2 — только GET. Методов записи здесь нет и быть не должно
(тест `test_client_has_no_write_methods`); обмен и обновление токена живут в `x/auth.py`.

Каждый HTTP-запрос проходит через `CallMeter` (бюджет до запроса, журнал после).
На 429 ждёт до времени из заголовка `x-rate-limit-reset` и повторяет.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol

import httpx

from xcopilot.clock import Clock
from xcopilot.x.errors import XAuthError, XNotFound, XUnavailable
from xcopilot.x.models import XMetrics, XPost, XUser

API_BASE = "https://api.x.com"

EP_POST = "GET /2/tweets/:id"
EP_POSTS = "GET /2/tweets"
EP_USER_BY_HANDLE = "GET /2/users/by/username/:username"
EP_ME = "GET /2/users/me"
EP_USER_POSTS = "GET /2/users/:id/tweets"

_FIELDS = "author_id,conversation_id,created_at,note_tweet,public_metrics,referenced_tweets"
_OWN_FIELDS = (
    "author_id,conversation_id,created_at,non_public_metrics,note_tweet,"
    "public_metrics,referenced_tweets"
)
_POST_PARAMS = {"tweet.fields": _FIELDS, "expansions": "author_id", "user.fields": "name,username"}
_USER_PARAMS = {"user.fields": "name,username"}
_MAX_IDS = 100
_PAGE_MIN, _PAGE_MAX = 5, 100
_DEFAULT_RESET_WAIT = 60.0

TokenProvider = Callable[[], Awaitable[str]]
Sleep = Callable[[float], Awaitable[None]]
Json = dict[str, Any]


class CallMeter(Protocol):
    """Учёт одного HTTP-вызова: `before` — проверка бюджета, `after` — журнал."""

    async def before(
        self, endpoint: str, resource_ids: Sequence[str] | None, max_resources: int
    ) -> None: ...

    async def after(
        self, endpoint: str, resource_ids: Sequence[str], http_status: int | None, ok: bool
    ) -> None: ...


def _int(d: Mapping[str, Any], key: str) -> int:
    v = d.get(key)
    return v if isinstance(v, int) else 0


def _opt_int(d: Mapping[str, Any] | None, key: str) -> int | None:
    if not d:
        return None
    v = d.get(key)
    return v if isinstance(v, int) else None


def parse_metrics(tweet: Mapping[str, Any]) -> XMetrics | None:
    pub = tweet.get("public_metrics")
    if not isinstance(pub, dict):
        return None
    non = tweet.get("non_public_metrics")
    non_pub = non if isinstance(non, dict) else None
    impressions = _opt_int(non_pub, "impression_count")
    if impressions is None:
        impressions = _opt_int(pub, "impression_count")
    return XMetrics(
        likes=_int(pub, "like_count"),
        reposts=_int(pub, "retweet_count"),
        replies=_int(pub, "reply_count"),
        quotes=_int(pub, "quote_count"),
        bookmarks=_int(pub, "bookmark_count"),
        impressions=impressions,
        url_clicks=_opt_int(non_pub, "url_link_clicks"),
        profile_clicks=_opt_int(non_pub, "user_profile_clicks"),
    )


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_tweet(tweet: Mapping[str, Any], users: Mapping[str, Mapping[str, Any]]) -> XPost:
    note = tweet.get("note_tweet")
    text = note.get("text") if isinstance(note, dict) else None
    if not isinstance(text, str):
        text = str(tweet.get("text", ""))
    author_id = tweet.get("author_id")
    author = users.get(author_id, {}) if isinstance(author_id, str) else {}
    reply_to = None
    refs = tweet.get("referenced_tweets")
    if isinstance(refs, list):
        for ref in refs:
            if isinstance(ref, dict) and ref.get("type") == "replied_to":
                reply_to = str(ref.get("id"))
    return XPost(
        id=str(tweet["id"]),
        text=text,
        author_handle=author.get("username"),
        author_name=author.get("name"),
        author_id=author_id if isinstance(author_id, str) else None,
        created_at=_parse_ts(tweet.get("created_at")),
        source="api",
        metrics=parse_metrics(tweet),
        conversation_id=tweet.get("conversation_id"),
        in_reply_to_id=reply_to,
    )


def _users(body: Json) -> dict[str, Mapping[str, Any]]:
    includes = body.get("includes")
    out: dict[str, Mapping[str, Any]] = {}
    if isinstance(includes, dict):
        for u in includes.get("users") or []:
            if isinstance(u, dict) and "id" in u:
                out[str(u["id"])] = u
    return out


def _parse_user(data: Mapping[str, Any]) -> XUser:
    return XUser(id=str(data["id"]), handle=str(data["username"]), name=data.get("name"))


class XApiV2:
    def __init__(
        self,
        http: httpx.AsyncClient,
        token: TokenProvider,
        *,
        clock: Clock,
        sleep: Sleep,
        max_rate_limit_waits: int = 3,
    ) -> None:
        self._http = http
        self._token = token
        self._clock = clock
        self._sleep = sleep
        self._max_waits = max_rate_limit_waits

    async def _get(
        self,
        endpoint: str,
        path: str,
        params: Mapping[str, str],
        *,
        meter: CallMeter | None,
        resource_ids: Sequence[str] | None,
        max_resources: int,
        result_ids: Callable[[Json], list[str]],
    ) -> Json:
        """GET с учётом в meter; на 429 ждёт сброса окна. Тело ответа или типизированная ошибка."""
        waits = 0
        while True:
            if meter is not None:
                await meter.before(endpoint, resource_ids, max_resources)
            headers = {"Authorization": f"Bearer {await self._token()}"}
            try:
                resp = await self._http.get(f"{API_BASE}{path}", params=params, headers=headers)
            except httpx.HTTPError as exc:
                if meter is not None:
                    await meter.after(endpoint, [], None, False)
                raise XUnavailable(f"X API недоступен: {endpoint}: {type(exc).__name__}") from None
            status = resp.status_code
            if status == 429 and waits < self._max_waits:
                if meter is not None:
                    await meter.after(endpoint, [], status, False)
                waits += 1
                await self._sleep(self._reset_wait(resp))
                continue
            if status != 200:
                if meter is not None:
                    await meter.after(endpoint, [], status, False)
                raise self._error(endpoint, status)
            try:
                body = resp.json()
            except ValueError:
                body = None
            if not isinstance(body, dict):
                if meter is not None:
                    await meter.after(endpoint, [], status, False)
                raise XUnavailable(f"X API: неожиданный ответ {endpoint}")
            if meter is not None:
                await meter.after(endpoint, result_ids(body), status, True)
            return body

    def _reset_wait(self, resp: httpx.Response) -> float:
        raw = resp.headers.get("x-rate-limit-reset")
        try:
            reset = float(raw) if raw is not None else None
        except ValueError:
            reset = None
        if reset is None:
            return _DEFAULT_RESET_WAIT
        return max(reset - self._clock.now().timestamp(), 0.0) + 1.0

    @staticmethod
    def _error(endpoint: str, status: int) -> Exception:
        if status == 401:
            return XAuthError(f"X API: токен не принят ({endpoint})")
        if status == 404:
            return XNotFound(f"X API: не найдено ({endpoint})")
        if status == 429:
            return XUnavailable(f"X API: лимит частоты не сбросился ({endpoint})")
        return XUnavailable(f"X API вернул {status} ({endpoint})")

    @staticmethod
    def _data_ids(body: Json) -> list[str]:
        data = body.get("data")
        if isinstance(data, dict):
            return [str(data["id"])] if "id" in data else []
        if isinstance(data, list):
            return [str(d["id"]) for d in data if isinstance(d, dict) and "id" in d]
        return []

    async def get_post(self, post_id: str, *, meter: CallMeter | None = None) -> XPost:
        body = await self._get(
            EP_POST,
            f"/2/tweets/{post_id}",
            _POST_PARAMS,
            meter=meter,
            resource_ids=[post_id],
            max_resources=1,
            result_ids=self._data_ids,
        )
        data = body.get("data")
        if not isinstance(data, dict):
            raise XNotFound(f"пост {post_id} не найден")
        return parse_tweet(data, _users(body))

    async def get_posts(self, ids: Sequence[str], *, meter: CallMeter | None = None) -> list[XPost]:
        """Пачка постов (до 100 за запрос); ненайденные пропускаются."""
        out: list[XPost] = []
        unique = list(dict.fromkeys(ids))
        for i in range(0, len(unique), _MAX_IDS):
            chunk = unique[i : i + _MAX_IDS]
            body = await self._get(
                EP_POSTS,
                "/2/tweets",
                {**_POST_PARAMS, "ids": ",".join(chunk)},
                meter=meter,
                resource_ids=chunk,
                max_resources=len(chunk),
                result_ids=self._data_ids,
            )
            users = _users(body)
            data = body.get("data")
            if isinstance(data, list):
                out += [parse_tweet(t, users) for t in data if isinstance(t, dict)]
        return out

    async def get_user(self, handle: str, *, meter: CallMeter | None = None) -> XUser:
        body = await self._get(
            EP_USER_BY_HANDLE,
            f"/2/users/by/username/{handle}",
            _USER_PARAMS,
            meter=meter,
            resource_ids=None,
            max_resources=1,
            result_ids=self._data_ids,
        )
        data = body.get("data")
        if not isinstance(data, dict):
            raise XNotFound(f"пользователь @{handle} не найден")
        return _parse_user(data)

    async def get_me(self, *, meter: CallMeter | None = None) -> XUser:
        body = await self._get(
            EP_ME,
            "/2/users/me",
            _USER_PARAMS,
            meter=meter,
            resource_ids=None,
            max_resources=1,
            result_ids=self._data_ids,
        )
        data = body.get("data")
        if not isinstance(data, dict):
            raise XAuthError("X API: не удалось узнать свой аккаунт")
        return _parse_user(data)

    async def get_own_posts(
        self,
        user_id: str,
        since_id: str | None = None,
        limit: int = 100,
        *,
        meter: CallMeter | None = None,
    ) -> list[XPost]:
        """Свои посты (новые первыми) с публичными и непубличными метриками, постранично."""
        out: list[XPost] = []
        token: str | None = None
        while len(out) < limit:
            page = min(max(limit - len(out), _PAGE_MIN), _PAGE_MAX)
            params = {"max_results": str(page), "tweet.fields": _OWN_FIELDS}
            if since_id:
                params["since_id"] = since_id
            if token:
                params["pagination_token"] = token
            body = await self._get(
                EP_USER_POSTS,
                f"/2/users/{user_id}/tweets",
                params,
                meter=meter,
                resource_ids=None,
                max_resources=page,
                result_ids=self._data_ids,
            )
            data = body.get("data")
            if isinstance(data, list):
                out += [parse_tweet(t, {}) for t in data if isinstance(t, dict)]
            meta = body.get("meta")
            token = meta.get("next_token") if isinstance(meta, dict) else None
            if not token:
                break
        return out[:limit]
