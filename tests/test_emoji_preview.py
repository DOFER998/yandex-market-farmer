import asyncio
from types import SimpleNamespace

from scripts.emoji_preview import build_preview


def test_preview_uses_bot_api_metadata_and_local_thumbnails(tmp_path):
    calls = []
    sticker = SimpleNamespace(custom_emoji_id='123', emoji='🕓', thumbnail=object())

    class Bot:
        async def get_sticker_set(self, name):
            calls.append(name)
            return SimpleNamespace(stickers=[sticker])

        async def get_custom_emoji_stickers(self, *, custom_emoji_ids):
            calls.extend(custom_emoji_ids)
            return [sticker]

        async def download(self, thumbnail, *, destination):
            destination.write_bytes(b'fake thumbnail')

    page = asyncio.run(build_preview(Bot(), tmp_path))
    text = page.read_text()
    assert calls[0] == 'tgiosicons'
    assert '5775896410780079073' in calls
    assert text.count('<article>') == 1
    assert 'src="123.webp"' in text
    assert 'tg://emoji?id=123' in text
