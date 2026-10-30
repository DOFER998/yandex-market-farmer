"""One active message per chat; stale operations cannot replace newer navigation."""

import asyncio
import re
from contextvars import ContextVar
from dataclasses import dataclass, field

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InputMediaPhoto, Message
from loguru import logger

from src.services.emoji import ALIASES, EmojiCatalog


@dataclass
class ChatScreen:
    message_id: int | None = None
    revision: int = 0
    photo: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ScreenService:
    def __init__(self, icons: EmojiCatalog) -> None:
        self.icons = icons
        self._request: ContextVar[tuple[int, int] | None] = ContextVar(
            'screen_revision', default=None
        )
        self._chats: dict[int, ChatScreen] = {}

    def begin(self, message: Message, *, callback: bool = True) -> int:
        state = self._chats.setdefault(message.chat.id, ChatScreen())
        if state.message_id is None and callback:
            state.message_id = message.message_id
        state.revision += 1
        self._request.set((message.chat.id, state.revision))
        return state.revision

    def request_revision(self, message: Message) -> int | None:
        value = self._request.get()
        return value[1] if value and value[0] == message.chat.id else None

    async def show(
        self, message: Message, text: str, keyboard: InlineKeyboardMarkup, revision: int
    ) -> None:
        state = self._chats[message.chat.id]
        async with state.lock:
            if revision != state.revision:
                return
            for attempt in range(2):
                if not self.icons.enabled:
                    text = re.sub(r'<tg-emoji[^>]*>(.*?)</tg-emoji>', r'\1', text)
                    keyboard = keyboard.model_copy(deep=True)
                    for row in keyboard.inline_keyboard:
                        for button in row:
                            if button.icon_custom_emoji_id:
                                name = next(
                                    (
                                        name
                                        for name, identifier in self.icons.ids.items()
                                        if identifier == button.icon_custom_emoji_id
                                    ),
                                    None,
                                )
                                if name:
                                    button.text = f'{ALIASES[name][0]} {button.text}'
                            button.icon_custom_emoji_id = None
                try:
                    if state.photo and state.message_id is not None:
                        await message.bot.delete_message(
                            chat_id=message.chat.id, message_id=state.message_id
                        )
                        state.message_id = None
                        state.photo = False
                    if state.message_id is None:
                        sent = await message.answer(
                            text, reply_markup=keyboard, parse_mode=ParseMode.HTML
                        )
                        state.message_id = sent.message_id
                    else:
                        await message.bot.edit_message_text(
                            chat_id=message.chat.id,
                            message_id=state.message_id,
                            text=text,
                            reply_markup=keyboard,
                            parse_mode=ParseMode.HTML,
                        )
                    return
                except TelegramBadRequest as exc:
                    reason = exc.message.lower()
                    if 'message is not modified' in reason:
                        return
                    if (
                        'message to edit not found' in reason
                        or "message can't be edited" in reason
                        or 'message to delete not found' in reason
                    ):
                        state.message_id = None
                        state.photo = False
                        if attempt == 0:
                            continue
                    if self.icons.enabled and ('emoji' in reason or 'premium' in reason):
                        self.icons.enabled = False
                        logger.warning('Telegram rejected custom emoji; using text-only controls')
                        if attempt == 0:
                            continue
                    raise

    async def show_qr(
        self,
        message: Message,
        png: bytes,
        caption: str,
        keyboard: InlineKeyboardMarkup,
        revision: int,
    ) -> None:
        state = self._chats[message.chat.id]
        async with state.lock:
            if state.revision != revision:
                return
            media = BufferedInputFile(png, filename='login.png')
            if state.message_id is None:
                sent = await message.answer_photo(
                    media, caption=caption, reply_markup=keyboard, parse_mode=ParseMode.HTML
                )
                state.message_id = sent.message_id
            else:
                await message.bot.edit_message_media(
                    chat_id=message.chat.id,
                    message_id=state.message_id,
                    media=InputMediaPhoto(media=media, caption=caption, parse_mode=ParseMode.HTML),
                    reply_markup=keyboard,
                )
            state.photo = True
