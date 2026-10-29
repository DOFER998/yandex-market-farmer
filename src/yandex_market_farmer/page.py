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


def page_context(text: str) -> AuthContext:
    if challenge_page(text):
        raise AccessDenied("Active challenge page; resolve manually in browser")

    def extract(key):
        match = re.search(r'"' + re.escape(key) + r'"\s*:\s*("(?:[^"\\]|\\.)*")', text)
        if not match:
            return ""
        try:
            return json.loads(match[1])
        except ValueError:
            raise ProtocolError("Invalid page context") from None

    sk, login = extract("sk"), extract("login")
    if not sk or not login:
        raise AuthRequired("Authenticated profile missing; run ymf login")
    version, glue = extract("-version"), extract("marketFrontGlue")
    if "-desktop." not in version or not glue:
        raise ProtocolError("Market page context missing or changed")
    return AuthContext(
        sk=sk, login=login, version=version, glue=glue, language=extract("lang") or "ru"
    )
