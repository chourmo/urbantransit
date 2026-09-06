from .agency_schema import AGENCY_BOOLEAN_COLS, AGENCY_DEFAULTS, AGENCY_SPEC
from .base import _ConfiguredGTFSFileParser


class GTFSAgencyParser(_ConfiguredGTFSFileParser):
    """Class to parse GTFS Agency.txt file"""

    filename: str = 'agency.txt'
    is_required: bool = True
    unique_id: str | None = 'agency_id'
    file_type = 'csv'
    spec = AGENCY_SPEC
    defaults = AGENCY_DEFAULTS
    boolean_cols = AGENCY_BOOLEAN_COLS
