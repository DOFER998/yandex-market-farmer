import io

import pytest

from scripts.diagnose_games import DiagnosticClient, inspect_games
from src.market.errors import ProtocolError, RateLimited
from src.market.models import Game, Hub


@pytest.mark.parametrize('operation', ['claim_game', 'claim_daily', 'balance', 'anything'])
def test_diagnostics_block_other_endpoints_before_network(operation):
    client = DiagnosticClient(None, io.StringIO())
    with pytest.raises(ValueError):
        client._api(operation)


def test_diagnostics_continue_schema_failure_but_stop_on_rate_limit():
    calls = []

    class Client:
        def games(self):
            return Hub(
                games=[Game(game_id=x, token='private') for x in ('a', 'b', 'c')], missions=[]
            )

        def record(self, data):
            assert 'private' not in str(data)

        def game_levels(self, game):
            calls.append(game)
            if game == 'a':
                raise ProtocolError('schema')
            raise RateLimited(60)

    with pytest.raises(RateLimited):
        inspect_games(Client())
    assert calls == ['a', 'b']
