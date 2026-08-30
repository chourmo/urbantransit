from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pandas as pd

WEEKDAYS = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]


def _week_year_epoch(year: int, week: int) -> int:
    """Calculate the Unix timestamp for Monday at 0:00 for a given ISO year and week."""
    # January 4th of any year is always in ISO week 1
    jan_4 = datetime(year, 1, 4, tzinfo=UTC)

    # Find the Monday of week 1
    # weekday(): Monday=0, Sunday=6
    # For week 1, we want the Monday of that week
    days_to_monday = jan_4.weekday()  # days since Monday
    week_1_monday = jan_4 - timedelta(days=days_to_monday)

    # Add (week - 1) * 7 days to reach the Monday of the requested week
    target_monday = week_1_monday + timedelta(weeks=week - 1)

    # Convert to Unix timestamp (assuming UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    unix_timestamp = int((target_monday - epoch).total_seconds())

    return unix_timestamp


def day_from_seconds(seconds: pd.Series) -> pd.Series:
    """Day from seconds integers"""
    day = 3600 * 24
    bins = range(0, day * 8, day)

    # replace times after midnight on sunday
    df = seconds.reset_index(drop=True)
    df.loc[df >= day * 7] = df - day * 7
    df.index = seconds.index

    results = pd.cut(df, bins=bins, labels=WEEKDAYS, right=False)
    return results.astype("string[pyarrow]")


def seconds_to_text(
    seconds: pd.Series, year: int, week: int, format: str = "%H:%M"
) -> pd.Series:
    """Format seconds to text based on format"""

    df = seconds.to_frame("seconds")
    df["time"] = pd.to_datetime(df["seconds"] + _week_year_epoch(year, week), unit="s")

    return df["time"].dt.strftime(format).astype("string[pyarrow]")


# ------------------------------------------------------------------------------------------------------
# class for a day hours minutes seconds


@dataclass
class DayTime:
    day: str
    hours: int
    minutes: int
    seconds: int = 0

    WEEKDAYS = [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ]

    def __post_init__(self):
        """validate values"""

        if self.day not in self.WEEKDAYS:
            raise ValueError(f"Invalid day: {self.day}. Must be one of {self.WEEKDAYS}")
        if self.hours < 0:
            raise ValueError(f"{self.hours} hour cannot be negative")
        if self.hours > 24:
            raise ValueError(f"{self.hours} hour must be smaller than 25")
        if self.minutes < 0:
            raise ValueError(f"{self.minutes} minutes cannot be negative")
        if self.minutes >= 60:
            raise ValueError(f"{self.minutes} minutes must be smaller than 60")
        if self.seconds < 0:
            raise ValueError(f"{self.seconds} seconds cannot be negative")
        if self.seconds >= 60:
            raise ValueError(f"{self.seconds} seconds must be smaller than 60")

    def day_index(self, day=None):

        if day is not None:
            return list.index(self.WEEKDAYS, day)
        return list.index(self.WEEKDAYS, self.day)

    @staticmethod
    def decompose(seconds: int) -> tuple[int, int, int, int]:
        """Decompose a seconds integer to a tuple of day, hours, minutes, seconds"""
        day = seconds // (24 * 3600)
        hours = (seconds - day * 24 * 3600) // 3600
        min_sec = seconds - day * 24 * 3600 - hours * 3600
        minutes, secs = min_sec // 60, min_sec % 60

        return day, hours, minutes, secs

    def to_seconds(self):
        """Return seconds from midnight"""
        return (
            self.day_index() * (24 * 3600)
            + self.hours * 3600
            + self.minutes * 60
            + self.seconds
        )

    def shift(self, hours=0, minutes=0, seconds=0):
        """Create a new DayTime by shifting by hours, minutes or seconds"""

        new_time = self.to_seconds() + hours * 3600 + minutes * 60 + seconds
        if new_time > 7 * 24 * 3600:
            raise ValueError("Cannot shift by more than a week")

        d, h, m, s = self.decompose(new_time)

        return DayTime(self.WEEKDAYS[d], h, m, s)
