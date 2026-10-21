"""When cold email may leave: weekdays and office hours in the tenant's
timezone. Harvey's quiet hours have no weekday notion; this hook does."""

from datetime import datetime, time

from zoneinfo import ZoneInfo


def make_cold_send_window(deliverability, now=None):
    tz = ZoneInfo(deliverability.timezone or "Europe/London")
    start = time.fromisoformat(deliverability.send_hours[0])
    end = time.fromisoformat(deliverability.send_hours[1])
    days = set(deliverability.send_days)

    def is_open() -> bool:
        current = (now() if now else datetime.now(tz)).astimezone(tz)
        return current.weekday() in days and start <= current.time() <= end

    return is_open
