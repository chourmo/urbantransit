import pandas as pd
import pyarrow as pa

from urbantransit.utils.logging import transitlog

from .base import _ConfiguredGTFSFileParser
from .calendar_schema import CALENDAR_BOOLEAN_COLS, CALENDAR_DEFAULTS, CALENDAR_SPEC


class GTFSCalendarParser(_ConfiguredGTFSFileParser):
    """Class to parse GTFS Calendar.txt file"""

    filename: str = 'calendar.txt'
    is_required: bool = False
    unique_id: str | None = 'service_id'
    file_type = 'csv'
    spec = CALENDAR_SPEC
    defaults = CALENDAR_DEFAULTS
    boolean_cols = CALENDAR_BOOLEAN_COLS

    WEEKDAYS = [
        'monday',
        'tuesday',
        'wednesday',
        'thursday',
        'friday',
        'saturday',
        'sunday',
    ]

    def expand(self) -> pd.Series:
        """expand calendar to individual dates"""
        calendar = self.data.copy()

        # dt pandas functions are slow with pyarrow data, use pa.compute
        days = pa.compute.days_between(
            pa.array(calendar['start_date']), pa.array(calendar['end_date'])
        )
        days = pa.compute.add(days, 1)
        calendar['days'] = pd.Series(days, index=calendar.index)

        # duplicate each row by the number of days
        calendar['_shift'] = calendar['days'].apply(lambda x: range(x))
        calendar = calendar.explode('_shift')

        # shift date
        shifted = pa.array(calendar['start_date']).cast(pa.int64())
        shifted = pa.compute.add(
            shifted,
            pa.array(
                pa.compute.multiply(24 * 3600, pa.array(calendar['_shift'])),
                type=pa.int64(),
            ),
        )
        shifted = shifted.cast(pa.timestamp(unit='s'))

        calendar['date'] = pd.Series(
            shifted, index=calendar.index, dtype=pd.ArrowDtype(pa.timestamp(unit='s'))
        )

        calendar = calendar.drop(columns=['_shift', 'start_date', 'end_date', 'days'])

        calendar['_dayofweek'] = calendar['date'].dt.dayofweek
        day_map = {num: day for num, day in zip(range(7), self.WEEKDAYS)}
        calendar['_dayofweek'] = calendar['_dayofweek'].map(day_map)

        mask = pd.Series(False, index=calendar.index)
        for day in self.WEEKDAYS:
            mask = mask | ((calendar['_dayofweek'] == day) & (calendar[day]))
        calendar = calendar.loc[mask, ['service_id', 'date']]

        transitlog.info(
            f"{self.base_name} : Calendar from {calendar['date'].dt.date.min()} to {calendar['date'].dt.date.max()}"
        )

        return calendar

    def has_inverted_start_end_dates(self) -> bool:
        df = self.data

        if df is None:
            return False

        mask = df['start_date'] > df['end_date']
        return mask.any()

    def fix_start_end_dates(self) -> pd.DataFrame | None:
        """Validate that start_date is before end_date, reverse start and end dates if not"""

        if self.data is None:
            return None

        df = self.data.copy()

        # invert start and end date if inverted
        mask = df['start_date'] > df['end_date']
        if not mask.any():
            return None

        temp = df.loc[mask, 'start_date'].copy()
        df.loc[mask, 'start_date'] = df['end_date']
        df.loc[mask, 'end_date'] = temp
        self.data = df

        return None
