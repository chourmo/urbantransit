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
