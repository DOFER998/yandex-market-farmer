import io
import sys
from types import SimpleNamespace

from pydantic import SecretStr

from scripts.terminal_qr import show_qr


def test_qr_uses_original_link_and_terminal_output_without_logging_secret(monkeypatch):
    recorded = {}

    class QRCode:
        def __init__(self, **kwargs):
            recorded.update(kwargs)

        def add_data(self, data):
            recorded['data'] = data

        def make(self, **kwargs):
            assert kwargs == {'fit': True}

        def print_ascii(self, *, out, tty, invert):
            assert not tty and invert
            out.write('██ QR ██\n')

    monkeypatch.setitem(
        sys.modules,
        'qrcode',
        SimpleNamespace(
            QRCode=QRCode,
            constants=SimpleNamespace(ERROR_CORRECT_M=0),
        ),
    )
    out = io.StringIO()
    show_qr(SecretStr('https://passport.yandex.ru/auth/magic?secret=123'), out)
    assert recorded['border'] == 4
    assert recorded['data'].endswith('secret=123')
    assert '██ QR ██' in out.getvalue()
    assert 'secret=123' not in out.getvalue()
