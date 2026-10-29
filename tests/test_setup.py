from datetime import datetime
from zoneinfo import ZoneInfo


def test_pacific_timezone_has_daylight_saving_data():
    pacific = ZoneInfo("America/Los_Angeles")
    assert datetime(2026, 1, 1, tzinfo=pacific).utcoffset().total_seconds() == -28800
    assert datetime(2026, 7, 1, tzinfo=pacific).utcoffset().total_seconds() == -25200
