"""Download a local contact sheet from Bot API; never send messages to Telegram."""

import asyncio
import os
from html import escape
from pathlib import Path

from aiogram import Bot
from loguru import logger

from src.common.config import Environment
from src.common.utils import setup_logging
from src.services.emoji import PINNED


async def build_preview(bot: Bot, output: Path) -> Path:
    pack = await bot.get_sticker_set('tgiosicons')
    pinned = await bot.get_custom_emoji_stickers(custom_emoji_ids=list(PINNED.values()))
    stickers = {
        s.custom_emoji_id: s
        for s in [*pack.stickers, *pinned]
        if s.custom_emoji_id and s.custom_emoji_id.isdecimal()
    }
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    semaphore = asyncio.Semaphore(4)

    async def card(identifier, sticker):
        async with semaphore:
            picture = escape(sticker.emoji or '—')
            if sticker.thumbnail:
                try:
                    target = output / f'{identifier}.webp'
                    await bot.download(sticker.thumbnail, destination=target)
                    picture = f'<img src="{target.name}" alt="{escape(sticker.emoji or "")}">'
                except Exception as exc:
                    logger.warning('Emoji thumbnail unavailable: {}', type(exc).__name__)
            return (
                f'<article>{picture}<p>{escape(sticker.emoji or "")}</p>'
                f'<code>{identifier}</code>'
                f'<p><a href="tg://emoji?id={identifier}">Открыть в Telegram</a></p></article>'
            )

    cards = await asyncio.gather(
        *(card(identifier, sticker) for identifier, sticker in stickers.items())
    )
    page = output / 'index.html'
    page.write_text(
        '<!doctype html><html lang="ru"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>tgiosicons — каталог Bot API</title><style>'
        'body{background:#202124;color:#eee;font:16px system-ui;margin:24px}'
        'main{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:20px}'
        'article{text-align:center;padding:16px;background:#303134;border-radius:12px}'
        'img{width:80px;height:80px;object-fit:contain}a{color:#8ab4f8}'
        'code{font-size:13px}</style><h1>Telegram iOS Icons</h1>'
        '<p>Миниатюры и ID получены через Bot API. Анимация доступна по ссылке в Telegram.</p>'
        f'<main>{"".join(cards)}</main></html>',
        encoding='utf-8',
    )
    return page


async def main() -> None:
    os.umask(0o077)
    setup_logging()
    try:
        async with Bot(Environment().bot.get_token()) as bot:
            async with asyncio.timeout(120):
                page = await build_preview(bot, Path('.secrets/emoji-preview/tgiosicons'))
        logger.info('Каталог эмодзи: {}', page.resolve())
    except Exception as exc:
        logger.error('Emoji preview failed: {}', type(exc).__name__)
        raise SystemExit(1) from None


if __name__ == '__main__':
    asyncio.run(main())
