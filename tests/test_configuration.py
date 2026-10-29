import pytest
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from src.common.config import Environment


def test_nested_dotenv_and_url_escaping(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / '.env').write_text(
        'BOT__OWNER_ID=123\nBOT__TOKEN=123:test\nPOSTGRES__PASSWORD="p@ss:/word"\n'
        'POSTGRES__PORT=5544\nMARKET__INTERVAL_SECONDS=4\nAPP__DEBUG=true\n'
    )
    env = Environment()
    assert env.bot.owner_id == 123
    assert env.bot.get_token() == '123:test'
    url = make_url(env.postgres.dsn)
    assert url.password == 'p@ss:/word'
    assert url.port == 5544
    assert env.market.interval_seconds == 4
    assert env.app.debug
    assert 'p@ss:/word' not in repr(env)
    assert '123:test' not in repr(env)


def test_config_errors_hide_credentials(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / '.env').write_text('BOT__TOKEN=private-token\nPOSTGRES__PASSWORD=private-db\n')
    with pytest.raises(ValidationError) as error:
        Environment()
    text = str(error.value)
    assert 'owner_id' in text
    assert 'private-token' not in text
    assert 'private-db' not in text
