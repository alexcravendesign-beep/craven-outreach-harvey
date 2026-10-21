from datetime import datetime
from zoneinfo import ZoneInfo

from harvey.craven.config import CravenDeliverabilityConfig
from harvey.craven.window import make_cold_send_window

LONDON = ZoneInfo("Europe/London")


def _at(y, m, d, hh, mm):
    return lambda: datetime(y, m, d, hh, mm, tzinfo=LONDON)


def test_weekday_office_hours_are_open():
    cfg = CravenDeliverabilityConfig()
    assert make_cold_send_window(cfg, now=_at(2026, 10, 21, 10, 0))()      # Wednesday
    assert not make_cold_send_window(cfg, now=_at(2026, 10, 21, 17, 0))()  # after 16:30
    assert not make_cold_send_window(cfg, now=_at(2026, 10, 21, 9, 0))()   # before 09:30
    assert not make_cold_send_window(cfg, now=_at(2026, 10, 24, 11, 0))()  # Saturday
