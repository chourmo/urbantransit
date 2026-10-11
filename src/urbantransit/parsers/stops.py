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
    required_fields = ("stop_id", "stop_name")
    enum_fields = {"location_type": range(6), "wheelchair_boarding": range(3)}
    range_fields = {"stop_lat": (-90, 90), "stop_lon": (-180, 180)}

    def fix_codes(self) -> None:
        """Fix stop_code and stop_id columns if they are mixed"""

        df = self.data

        if 'stop_code' not in df.columns:
            return None  # noqa: RET501

        # swap stop_code and stop_id
        df = df.rename(columns={'stop_code': 'stop_id', 'stop_id': 'stop_code'})

        self.data = df

    def validate_custom(self) -> None:
        location_type = self.data["location_type"].fillna(0)
        coordinates = self.data[["stop_lat", "stop_lon"]]
        if ((location_type == 0) & coordinates.isna().any(axis=1)).any():
            raise ValueError(
                "stop_lat and stop_lon are required for stops with "
                "location_type 0 in stops.txt"
            )
