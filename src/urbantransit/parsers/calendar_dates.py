from .base import _ConfiguredGTFSFileParser
from .calendar_dates_schema import (
    CALENDAR_DATES_BOOLEAN_COLS,
    CALENDAR_DATES_DEFAULTS,
    CALENDAR_DATES_SPEC,
)


class GTFSCalendarDatesParser(_ConfiguredGTFSFileParser):
    """Class to parse GTFS CalendarDates.txt file"""

    filename: str = 'calendar_dates.txt'
    is_required: bool = False
    unique_id: str | None = 'service_id'
    file_type = 'csv'
    spec = CALENDAR_DATES_SPEC
    defaults = CALENDAR_DATES_DEFAULTS
    boolean_cols = CALENDAR_DATES_BOOLEAN_COLS
