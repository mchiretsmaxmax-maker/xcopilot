"""Этап 6: ссылка авторизации OAuth 2.0 PKCE и разбор адреса возврата."""

import base64
import hashlib
import re
from urllib.parse import parse_qs, urlsplit

import pytest

from xcopilot.x.auth import build_auth_url, parse_callback
from xcopilot.x.errors import XAuthError, XStateMismatch

REDIRECT = "https://example.org/x/callback"


def test_pkce_auth_url_has_challenge_state_and_read_only_scopes() -> None:
    url, state, verifier = build_auth_url("client-123", REDIRECT)
    parts = urlsplit(url)
    assert (parts.scheme, parts.netloc, parts.path) == ("https", "x.com", "/i/oauth2/authorize")
    q = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert q["response_type"] == "code"
    assert q["client_id"] == "client-123"
    assert q["redirect_uri"] == REDIRECT
    assert q["scope"].split() == ["tweet.read", "users.read", "offline.access"]
    assert "write" not in q["scope"]
    assert q["state"] == state
    assert len(state) >= 32
    assert re.fullmatch(r"[A-Za-z0-9\-._~]{43,128}", verifier)
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    assert q["code_challenge"] == expected.decode().rstrip("=")
    assert q["code_challenge_method"] == "S256"
    assert verifier not in url
    _url2, state2, verifier2 = build_auth_url("client-123", REDIRECT)
    assert (state2, verifier2) != (state, verifier)


def test_state_mismatch_rejected() -> None:
    with pytest.raises(XStateMismatch):
        parse_callback(f"{REDIRECT}?state=evil&code=abc", "good-state")
    with pytest.raises(XStateMismatch):
        parse_callback(f"{REDIRECT}?code=abc", "good-state")
    assert parse_callback(f"{REDIRECT}?state=good-state&code=abc", "good-state") == "abc"
    # пользователь мог прислать адрес с пробелами и переносами
    assert parse_callback(f"  {REDIRECT}?code=abc&state=good-state\n", "good-state") == "abc"


def test_callback_denied_or_without_code() -> None:
    with pytest.raises(XAuthError):
        parse_callback(f"{REDIRECT}?error=access_denied&state=s", "s")
    with pytest.raises(XAuthError):
        parse_callback(f"{REDIRECT}?state=s", "s")
    with pytest.raises(XAuthError):
        parse_callback("не адрес", "s")
