"""One active message per chat; stale operations cannot replace newer navigation."""

import asyncio
import re
from contextvars import ContextVar
from dataclasses import dataclass, field

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InputMediaPhoto, Message
from loguru import logger

from src.services.emoji import ALIASES, EmojiCatalog


@dataclass
class ChatScreen:
    message_id: int | None = None
    revision: int = 0
    photo: bool = False
    rendered: tuple[str, str] | None = None
    retired: set[int] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ScreenService:
    def __init__(self, icons: EmojiCatalog) -> None:
        self.icons = icons
        self._request: ContextVar[tuple[int, int] | None] = ContextVar(
            'screen_revision', default=None
        )
        self._chats: dict[int, ChatScreen] = {}

    def begin(self, message: Message, *, callback: bool = True, activate: bool = False) -> int:
        state = self._chats.setdefault(message.chat.id, ChatScreen())
        if activate and callback and state.message_id != message.message_id:
            if state.message_id is not None:
                state.retired.add(state.message_id)
            state.retired.discard(message.message_id)
            state.message_id = message.message_id
            state.photo = bool(getattr(message, 'photo', None))
            state.rendered = None
        if state.message_id is None and callback:
            state.message_id = message.message_id
            state.photo = bool(getattr(message, 'photo', None))
        state.revision += 1
        self._request.set((message.chat.id, state.revision))
        return state.revision

    def request_revision(self, message: Message) -> int | None:
        value = self._request.get()
        return value[1] if value and value[0] == message.chat.id else None

    def _content(self, text, keyboard):
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
        return text, keyboard

    @staticmethod
    async def _retire(message: Message, state: ChatScreen) -> None:
        for identifier in tuple(state.retired):
            try:
                await message.bot.edit_message_reply_markup(
                    chat_id=message.chat.id, message_id=identifier, reply_markup=None
                )
            except TelegramAPIError as exc:
                # Old/deleted menu cleanup must not undo the newly opened screen.
                logger.debug('Previous menu cleanup skipped: error={}', type(exc).__name__)
            state.retired.discard(identifier)

    async def show(
        self, message: Message, text: str, keyboard: InlineKeyboardMarkup, revision: int
    ) -> None:
        state = self._chats[message.chat.id]
        async with state.lock:
            if revision != state.revision:
                return
            for attempt in range(3):
                text, keyboard = self._content(text, keyboard)
                rendered = (text, keyboard.model_dump_json(exclude_none=True))
                if not state.photo and state.message_id is not None and state.rendered == rendered:
                    await self._retire(message, state)
                    return
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
                    state.rendered = rendered
                    await self._retire(message, state)
                    return
                except TelegramBadRequest as exc:
                    reason = exc.message.lower()
                    if 'message is not modified' in reason:
                        state.rendered = rendered
                        await self._retire(message, state)
                        return
                    if (
                        'message to edit not found' in reason
                        or "message can't be edited" in reason
                        or 'message to delete not found' in reason
                        or "message can't be deleted" in reason
                        or 'there is no text in the message' in reason
                    ):
                        state.message_id = None
                        state.photo = False
                        if attempt < 2:
                            continue
                    if self.icons.enabled and ('emoji' in reason or 'premium' in reason):
                        self.icons.enabled = False
                        logger.warning('Telegram rejected custom emoji; using text-only controls')
                        if attempt < 2:
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
            for attempt in range(3):
                caption, keyboard = self._content(caption, keyboard)
                media = BufferedInputFile(png, filename='login.png')
                try:
                    if state.message_id is None:
                        sent = await message.answer_photo(
                            media, caption=caption, reply_markup=keyboard, parse_mode=ParseMode.HTML
                        )
                        state.message_id = sent.message_id
                    else:
                        await message.bot.edit_message_media(
                            chat_id=message.chat.id,
                            message_id=state.message_id,
                            media=InputMediaPhoto(
                                media=media, caption=caption, parse_mode=ParseMode.HTML
                            ),
                            reply_markup=keyboard,
                        )
                    state.photo = True
                    state.rendered = None
                    return
                except TelegramBadRequest as exc:
                    reason = exc.message.lower()
                    if 'message is not modified' in reason:
                        state.photo = True
                        return
                    if 'message to edit not found' in reason or "message can't be edited" in reason:
                        state.message_id = None
                        state.photo = False
                        if attempt < 2:
                            continue
                    if self.icons.enabled and ('emoji' in reason or 'premium' in reason):
                        self.icons.enabled = False
                        logger.warning(
                            'Telegram rejected QR custom emoji; using text-only controls'
                        )
                        if attempt < 2:
                            continue
                    raise
