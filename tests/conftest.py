from pathlib import Path

import pytest

from market_calendar import OfficialMarketCalendar


@pytest.fixture
def engine_calendar(monkeypatch):
    """Existing engine-model tests use the checked-in official session fixture.

    Close-gate tests supply their own calendar files and do not use this fixture.
    No network refresh or production guard is bypassed.
    """
    import prediction_engine.engine as engine
    path = Path(__file__).resolve().parents[1] / 'reports/official_market_calendar.json'
    monkeypatch.setattr(engine, 'OfficialMarketCalendar',
                        lambda *a, **kw: OfficialMarketCalendar(path, auto_refresh=False, allow_network=False))
