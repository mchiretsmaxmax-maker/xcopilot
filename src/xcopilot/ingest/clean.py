"""Чистка текста: пробелы, невидимые символы, служебные параметры ссылок."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_KEYS = {
    "fbclid",
    "gclid",
    "yclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "ref_src",
    "ref_url",
}
_INVISIBLE_CHARS = "\u200b\u200c\u200d\u2060\ufeff"
_URL_RE = re.compile(r"https?://\S+")


def strip_tracking(url: str) -> str:
    parts = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not (
            key.startswith("utm_")
            or key in _TRACKING_KEYS
            or (
                parts.hostname
                in {
                    "x.com",
                    "twitter.com",
                    "www.x.com",
                    "www.twitter.com",
                    "mobile.x.com",
                    "mobile.twitter.com",
                }
                and key in {"s", "t"}
            )
        )
    ]
    query_string = urlencode(query)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query_string, parts.fragment))


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = text.translate(str.maketrans("", "", _INVISIBLE_CHARS))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t\u00a0]+", " ", line)
        lines.append(line.strip(" "))
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = _URL_RE.sub(lambda match: strip_tracking(match.group(0)), text)
    return text.strip()
