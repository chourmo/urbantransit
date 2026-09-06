from typing import Any

import pyarrow as pa

FEED_INFO_SPEC: dict[str, Any] = {
    'feed_publisher_name': pa.string(),
    'feed_publisher_url': pa.string(),
    'feed_lang': pa.string(),
    'feed_start_date': pa.timestamp('s'),
    'feed_end_date': pa.timestamp('s'),
    'feed_version': pa.string(),
    'default_lang': pa.string(),
    'feed_contact_email': pa.string(),
    'feed_contact_url': pa.string(),
}
FEED_INFO_DEFAULTS: dict[str, Any] | None = None
FEED_INFO_BOOLEAN_COLS: dict[str, tuple[Any, Any, Any]] | None = None
