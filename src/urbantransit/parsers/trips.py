from .base import _ConfiguredGTFSFileParser
from .trips_schema import TRIPS_BOOLEAN_COLS, TRIPS_DEFAULTS, TRIPS_SPEC


class GTFSTripsParser(_ConfiguredGTFSFileParser):
    """Class to parse GTFS Trips.txt file"""

    filename: str = 'trips.txt'
    is_required: bool = True
    unique_id: str | None = 'trip_id'
    file_type = 'csv'
    spec = TRIPS_SPEC
    defaults = TRIPS_DEFAULTS
    boolean_cols = TRIPS_BOOLEAN_COLS
