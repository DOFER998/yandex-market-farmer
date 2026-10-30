from types import SimpleNamespace

from src.database.models import Account
from src.services.emoji import EmojiCatalog
from src.views.accounts import connection_text, name_prompt
from src.views.qr_login import (
    login_expired,
    login_instructions,
    login_loading,
    login_result,
    login_saved,
)


def test_onboarding_uses_shared_sections_and_preserves_user_text():
    icons = EmojiCatalog()
    texts = [
        name_prompt(icons),
        name_prompt(icons, rename=True, invalid=True),
        connection_text(Account(id='test', display_name='<Family & friends>'), icons, created=True),
        login_loading(icons),
        login_instructions(icons),
        login_expired(icons),
        login_saved(icons),
        login_result(SimpleNamespace(error='SessionError'), icons),
    ]
    for text in texts:
        assert '<blockquote>' in text
        assert '<tg-emoji' in text
        assert text.count('<blockquote') == text.count('</blockquote>')
    assert '&lt;Family &amp; friends&gt;' in texts[2]
    assert 'Аккаунт создан' in texts[2]
    assert len(login_instructions(icons)) < 1024


def test_verified_login_is_escaped_and_requires_explicit_save():
    attempt = SimpleNamespace(error=None, login='<owner&name>', balance=0)
    text = login_result(attempt)
    assert '&lt;owner&amp;name&gt;' in text
    assert '<owner' not in text
    assert '0 монет' in text
    assert 'Сохранить подключение?' in text
    assert 'Автосбор останется выключенным' in text
    assert 'Подключение сохранено' not in text


def test_onboarding_can_render_without_premium_emoji():
    icons = EmojiCatalog(enabled=False)
    assert '<tg-emoji' not in login_instructions(icons)
    assert '🔗' in login_instructions(icons)
    assert '<blockquote>' in name_prompt(icons)
