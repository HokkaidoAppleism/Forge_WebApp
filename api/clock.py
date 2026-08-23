"""What day it is, from the player's side.

Its own module because two routes need it and a shared rule written twice is a
rule that drifts -- which this codebase has already paid for three times, each
one a UTC-versus-local mistake: the review queue serving one question forever,
22 of 52 progress rows filed under the wrong date, and 30 of 53 flashcards
grouped under a day nobody played.

The desktop build could ask the operating system, because the machine running
the code was the machine the player was sitting at. A server cannot: it runs in
whatever region it was deployed to, so "today" has to be told to it.
"""

import datetime
import zoneinfo


def local_day(tz_name):
    """Today's date in the named IANA zone, falling back to UTC.

    An unknown or missing zone falls back rather than erroring. Getting the day
    wrong by a few hours is a bad streak count; refusing to record the answer
    at all is a lost answer, and the second is worse.
    """
    try:
        tz = zoneinfo.ZoneInfo(tz_name) if tz_name else datetime.timezone.utc
    except Exception:
        tz = datetime.timezone.utc
    return datetime.datetime.now(tz).date()
