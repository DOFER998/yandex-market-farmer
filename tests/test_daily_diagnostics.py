import io

import pytest

from scripts.diagnose_daily import DiagnosticClient
from src.market.errors import ClaimsDisabled


@pytest.mark.parametrize('operation', ['claim_daily', 'claim_game', 'hub', 'anything'])
def test_daily_diagnostics_disallow_non_read_endpoints(operation):
    client = DiagnosticClient(None, io.StringIO())
    with pytest.raises(ValueError):
        client._api(operation)


def test_daily_diagnostics_cannot_claim():
    client = DiagnosticClient(None, io.StringIO())
    with pytest.raises(ClaimsDisabled):
        client.claim_daily()
