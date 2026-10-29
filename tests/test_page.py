import json

import pytest

from src.market.errors import ProtocolError
from src.market.page import page_context


def test_version_search_matches_go_skipping_unrelated_versions():
    html = (
        '<script>{"-version":"unrelated-module", "sk":"token-secret",'
        '"login":"login-secret", "marketFrontGlue":"glue-secret"}</script>'
        '<script>{"-version":"2026-desktop.123"}</script>'
    )
    assert page_context(html).version == '2026-desktop.123'


def test_diagnostics_contain_only_counts_and_flags():
    from src.market.page import context_diagnostics

    html = (
        '{"-version":"wrong", "sk":"token-secret","login":"login-secret",'
        '"marketFrontGlue":"glue-secret"}'
    )
    result = context_diagnostics(html)
    assert result['version_matches'] == 1
    assert result['desktop_version_matches'] == 0
    assert result['front_glue_present'] is True
    assert all(type(value) in (bool, int) for value in result.values())
    assert 'secret' not in json.dumps(result)
    with pytest.raises(ProtocolError) as error:
        page_context(html + '{"-version":"another"}')
    assert 'secret' not in str(error.value)


def test_missing_glue_is_not_fabricated():
    with pytest.raises(ProtocolError):
        page_context('{"sk":"s","login":"a","-version":"v-desktop.1"}')


def test_single_non_desktop_version_is_preserved_verbatim():
    html = json.dumps(
        {'sk': 's', 'login': 'a', 'marketFrontGlue': 'g', '-version': '2026.10.release-17'}
    )
    assert page_context(html).version == '2026.10.release-17'


@pytest.mark.parametrize(
    'versions',
    [[], [''], ['v1', 'v2'], ['a-desktop.1', 'b-desktop.2'], ['v\r\nInjected: value'], ['v\x00']],
)
def test_absent_ambiguous_or_unsafe_version_is_rejected(versions):
    html = json.dumps({'sk': 's', 'login': 'a', 'marketFrontGlue': 'g'})
    html += ''.join(json.dumps({'-version': value}) for value in versions)
    with pytest.raises(ProtocolError):
        page_context(html)
