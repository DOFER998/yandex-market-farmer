"""Recognize active challenge pages without matching strings in application bundles."""

import json
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

from .errors import AccessDenied, AuthRequired, ProtocolError
from .models import AuthContext


def challenge_url(url: str) -> bool:
    try:
        path = urlsplit(url).path.rstrip("/").lower()
    except ValueError:
        return False
    return path in ("/showcaptcha", "/checkcaptcha", "/captcha")


class _ChallengeHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.form = False
        self.in_title = False
        self.title = []

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            action = dict(attrs).get("action") or ""
            self.form |= challenge_url(action)
        if tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)


def challenge_page(text: str) -> bool:
    parser = _ChallengeHTML()
    parser.feed(text)
    title = " ".join("".join(parser.title).casefold().split())
    return parser.form or title in {
        "ой!",
        "ой",
        "подтвердите, что вы не робот",
        "подтвердите, что вы не робот — яндекс",
        "are you not a robot?",
        "captcha",
        "smartcaptcha",
    }


def _values(text: str, key: str) -> list[str]:
    pattern = r'"' + re.escape(key) + r'"\s*:\s*("(?:[^"\\]|\\.)*")'
    try:
        return [json.loads(match[1]) for match in re.finditer(pattern, text)]
    except ValueError:
        raise ProtocolError("Invalid page context") from None


def _desktop_versions(text: str) -> list[str]:
    # Go appVersionRegex selects the first matching desktop version, not the first -version.
    return [value for value in _values(text, "-version") if re.fullmatch(r".+-desktop\..+", value)]


def context_diagnostics(text: str) -> dict[str, bool | int]:
    """Only fixed keys and counts/flags; never expose HTML, identifiers or token values."""
    return {
        "challenge_detected": challenge_page(text),
        "sk_present": bool(next(iter(_values(text, "sk")), "")),
        "login_present": bool(next(iter(_values(text, "login")), "")),
        "version_matches": len(_values(text, "-version")),
        "desktop_version_matches": len(_desktop_versions(text)),
        "front_glue_present": bool(next(iter(_values(text, "marketFrontGlue")), "")),
        "front_glue_key_mentioned": "marketFrontGlue" in text,
        "desktop_marker_mentioned": "-desktop." in text,
    }


def page_context(text: str) -> AuthContext:
    if challenge_page(text):
        raise AccessDenied("Active challenge page; resolve manually in browser")

    def extract(key):
        return next(iter(_values(text, key)), "")

    sk, login = extract("sk"), extract("login")
    if not sk or not login:
        raise AuthRequired("Authenticated profile missing; run ymf login")
    version = next(iter(_desktop_versions(text)), "")
    glue = extract("marketFrontGlue")
    if not version or not glue:
        missing = []
        if not version:
            missing.append("desktop_version")
        if not glue:
            missing.append("marketFrontGlue")
        raise ProtocolError(
            "Market page context missing: " + ", ".join(missing) + "; run ymf diagnose-auth"
        )
    return AuthContext(
        sk=sk, login=login, version=version, glue=glue, language=extract("lang") or "ru"
    )
