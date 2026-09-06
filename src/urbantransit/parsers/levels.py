from .base import _ConfiguredGTFSFileParser
from .levels_schema import LEVELS_BOOLEAN_COLS, LEVELS_DEFAULTS, LEVELS_SPEC


class GTFSLevelsParser(_ConfiguredGTFSFileParser):
    """Class to parse GTFS Levels.txt file"""

    filename: str = 'levels'
    is_required: bool = False
    unique_id: str | None = 'level_id'
    file_type = 'csv'
    spec = LEVELS_SPEC
    defaults = LEVELS_DEFAULTS
    boolean_cols = LEVELS_BOOLEAN_COLS
