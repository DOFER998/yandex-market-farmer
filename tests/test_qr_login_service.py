import asyncio
from types import SimpleNamespace

from pydantic import SecretStr

from src.services.qr_login import QrLoginService


def test_qr_attempt_is_bound_to_chat_account_nonce_and_verified_before_ready():
    async def scenario():
        sessions = []

        class Session:
            closed = False

            def close(self):
                self.closed = True

        def factory(_):
            result = Session()
            sessions.append(result)
            return result

        class Auth:
            def __init__(self, session):
                pass

            def start(self):
                return SecretStr('https://passport.yandex.ru/auth')

            def poll(self):
                return True

        class Client:
            def __init__(self, session, *, allow_claims):
                assert not allow_claims

            def check_auth(self):
                return SimpleNamespace(login='owner')

            def balance(self):
                return 96

        service = QrLoginService(
            session_factory=factory, auth_factory=Auth, client_factory=Client, poll_seconds=0
        )
        attempt = await service.begin(123, 'main')
        assert attempt.balance is None
        assert service.get(999, 'main', attempt.nonce) is None
        assert service.get(123, 'other', attempt.nonce) is None
        ready = asyncio.Event()

        async def callback(value):
            assert value.balance == 96 and value.login == 'owner'
            ready.set()

        service.watch(123, callback)
        await asyncio.wait_for(ready.wait(), 1)
        await service.cancel(123)
        assert sessions[0].closed
        assert service.get(123, 'main', attempt.nonce) is None

    asyncio.run(scenario())


def test_failed_market_verification_never_marks_attempt_ready():
    from src.market.errors import AccessDenied

    async def scenario():
        session = SimpleNamespace(close=lambda: None)

        class Auth:
            def __init__(self, _):
                pass

            def start(self):
                return SecretStr('https://passport.yandex.ru/auth')

            def poll(self):
                return True

        class Client:
            def __init__(self, *args, **kwargs):
                pass

            def check_auth(self):
                raise AccessDenied('private response')

        service = QrLoginService(
            session_factory=lambda _: session,
            auth_factory=Auth,
            client_factory=Client,
            poll_seconds=0,
        )
        attempt = await service.begin(123, 'main')
        done = asyncio.Event()

        async def callback(value):
            assert value.error == 'AccessDenied'
            assert value.balance is None
            done.set()

        service.watch(123, callback)
        await asyncio.wait_for(done.wait(), 1)
        assert attempt.balance is None
        await service.close()

    asyncio.run(scenario())


def test_qr_png_is_valid_square_image():
    import io

    import pytest

    image = pytest.importorskip('PIL.Image')
    from src.common.utils.qr import qr_png

    data = qr_png(SecretStr('https://passport.yandex.ru/auth/magic?test=1'))
    with image.open(io.BytesIO(data)) as picture:
        assert picture.format == 'PNG'
        assert picture.width == picture.height
        assert picture.width > 200
        picture.verify()


def test_qr_is_monochrome_with_clear_border_and_no_auth_metadata():
    from io import BytesIO

    from PIL import Image, ImageChops

    from src.common.utils.qr import qr_png

    link = SecretStr('https://passport.yandex.ru/auth/magic?track_id=private-test-only')
    with Image.open(BytesIO(qr_png(link))) as image:
        red, green, blue = image.convert('RGB').split()
        assert ImageChops.difference(red, green).getbbox() is None
        assert ImageChops.difference(green, blue).getbbox() is None
        for strip in (
            (0, 0, image.width, 48),
            (0, image.height - 48, image.width, image.height),
            (0, 0, 48, image.height),
            (image.width - 48, 0, image.width, image.height),
        ):
            assert image.crop(strip).convert('L').getextrema() == (255, 255)
        assert not image.info
