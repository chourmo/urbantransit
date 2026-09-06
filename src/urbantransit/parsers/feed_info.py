from .base import _ConfiguredGTFSFileParser
from .feed_info_schema import (
    FEED_INFO_BOOLEAN_COLS,
    FEED_INFO_DEFAULTS,
    FEED_INFO_SPEC,
)


class GTFSFeedInfoParser(_ConfiguredGTFSFileParser):
    """Class to parse GTFS FeedInfo.txt file"""

    filename: str = 'feed_info'
    is_required: bool = False
    unique_id: str | None = None
    file_type = 'csv'
    spec = FEED_INFO_SPEC
    defaults = FEED_INFO_DEFAULTS
    boolean_cols = FEED_INFO_BOOLEAN_COLS
