from dataclasses import dataclass, field
from html import escape

# Unicode fallbacks for the curated Telegram icons.
ALIASES = {
    'home': ('🏠', '🏡'),
    'account': ('👤', '👥'),
    'add': ('➕', '✚'),
    'balance': ('💰', '🪙', '💳'),
    'collect': ('🎁', '🎉'),
    'rename': ('🖌️',),
    'history': ('🕓', '🕘', '🕒', '📋', '🕓', '🗂', '📜', '🕰', '⏱', '📄'),
    'settings': ('⚙️', '⚙', '🔧'),
    'back': ('⬅️', '⬅', '◀️'),
    'stats': ('📊',),
    'refresh': ('🔄', '🔃'),
    'connect': ('🔗', '🔑', '🔐', '🗝', '🔒', '🌐', '🔓'),
    'loading': ('⏳', '⌛'),
    'notify': ('🔔',),
    'error': ('⚠️',),
    'delete': ('🗑️',),
    'confirm': ('✅',),
}


# Explicitly provided by the owner; keep these even if pack lookup fails.
PINNED = {
    'rename': '6050847684355428245',
    'history': '5775896410780079073',
    'connect': '5895364284782743985',
}


# Selected visually from the Bot API tgiosicons export, 2026-10-29.
# Duplicate Unicode labels in this pack represent different images.
PACK_ICONS = {
    'home': '6042137469204303531',
    'account': '6035084557378654059',
    'add': '6033108709213736873',
    'balance': '5904462880941545555',
    'collect': '5773677501825945508',
    'settings': '5904258298764334001',
    'back': '5960671702059848143',
    'stats': '5936143551854285132',
    'refresh': '5769248574499983619',
    'loading': '5891211339170326418',
    'notify': '6039486778597970865',
    'error': '5774077015388852135',
    'delete': '6039522349517115015',
    'confirm': '5774022692642492953',
}


@dataclass
class EmojiCatalog:
    ids: dict[str, str] = field(default_factory=lambda: PACK_ICONS | PINNED)
    enabled: bool = True

    def icon_id(self, name: str) -> str | None:
        return self.ids.get(name) if self.enabled else None

    def heading(self, name: str, text: str) -> str:
        if identifier := self.icon_id(name):
            return f'<tg-emoji emoji-id="{identifier}">{escape(ALIASES[name][0])}</tg-emoji> {text}'
        return f'{escape(ALIASES[name][0])} {text}'
