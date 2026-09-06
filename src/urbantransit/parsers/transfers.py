from .base import _ConfiguredGTFSFileParser
from .transfers_schema import (
    TRANSFERS_BOOLEAN_COLS,
    TRANSFERS_DEFAULTS,
    TRANSFERS_SPEC,
)


class GTFSTransfersParser(_ConfiguredGTFSFileParser):
    """Class to parse GTFS Transfers.txt file"""

    filename: str = 'transfers'
    is_required: bool = False
    unique_id: str | None = None
    file_type = 'csv'
    spec = TRANSFERS_SPEC
    defaults = TRANSFERS_DEFAULTS
    boolean_cols = TRANSFERS_BOOLEAN_COLS
