from src.services.emoji import ALIASES, EmojiCatalog
from src.views.fragments import section


def test_menu_icons_are_available_without_network():
    icons = EmojiCatalog()
    assert all(icons.icon_id(name) for name in ALIASES)
    assert icons.icon_id('balance') == '5904462880941545555'
    assert icons.icon_id('add') == '6033108709213736873'
    assert icons.icon_id('account') == '6035084557378654059'


def test_error_section_can_render_with_catalog_and_fallback():
    icons = EmojiCatalog()
    assert '<tg-emoji' in section('Ошибка', ['Повтори позже'], icon='error', icons=icons)
    icons.enabled = False
    text = section('Ошибка', ['Повтори позже'], icon='error', icons=icons)
    assert '⚠️' in text
    assert '<tg-emoji' not in text
