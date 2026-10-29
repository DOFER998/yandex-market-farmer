"""Telegram HTML sections, following the reference bot's game screens."""

from html import escape

from src.services.emoji import EmojiCatalog

GAP = '\n\n'


def heading(text: str, icon: str, icons: EmojiCatalog | None = None) -> str:
    return f'<b>{(icons or EmojiCatalog()).heading(icon, escape(text))}</b>'


def row(label: str, value: str) -> str:
    return f'• <b>{escape(label)}</b>: {value}'


def quote(rows: list[str], *, expandable: bool = False) -> str:
    attribute = ' expandable' if expandable else ''
    return f'<blockquote{attribute}>{"\n".join(rows)}</blockquote>'


def section(
    text: str,
    rows: list[str],
    *,
    icon: str,
    icons: EmojiCatalog | None = None,
    expandable: bool = False,
) -> str:
    return heading(text, icon, icons) + '\n' + quote(rows, expandable=expandable)
