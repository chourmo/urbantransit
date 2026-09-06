from .base import _ConfiguredGTFSFileParser
from .stops_schema import STOPS_BOOLEAN_COLS, STOPS_DEFAULTS, STOPS_SPEC


class GTFSStopsParser(_ConfiguredGTFSFileParser):
    """Class to parse GTFS Stops.txt file"""

    filename: str = 'stops.txt'
    is_required: bool = True
    unique_id: str | None = 'stop_id'
    file_type = 'csv'
    spec = STOPS_SPEC
    defaults = STOPS_DEFAULTS
    boolean_cols = STOPS_BOOLEAN_COLS

    def fix_codes(self) -> None:
        """Fix stop_code and stop_id columns if they are mixed"""

        df = self.data

        if 'stop_code' not in df.columns:
            return None  # noqa: RET501

        # swap stop_code and stop_id
        df = df.rename(columns={'stop_code': 'stop_id', 'stop_id': 'stop_code'})

        self.data = df
