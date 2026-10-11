from .base import _ConfiguredGTFSFileParser
from .routes_schema import ROUTES_BOOLEAN_COLS, ROUTES_DEFAULTS, ROUTES_SPEC


class GTFSRoutesParser(_ConfiguredGTFSFileParser):
    """Class to parse GTFS Routes.txt file"""

    filename: str = 'routes.txt'
    is_required: bool = True
    unique_id: str | None = 'route_id'
    file_type = 'csv'
    spec = ROUTES_SPEC
    defaults = ROUTES_DEFAULTS
    boolean_cols = ROUTES_BOOLEAN_COLS
    required_fields = ("route_id", "route_type")
    enum_fields = {"route_type": range(12)}

    def validate_custom(self) -> None:
        names = self.data[["route_short_name", "route_long_name"]]
        if names.isna().all(axis=1).any():
            raise ValueError(
                "at least one of route_short_name or route_long_name is required "
                "in routes.txt"
            )
