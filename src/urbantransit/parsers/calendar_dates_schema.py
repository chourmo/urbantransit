from typing import Any

import pyarrow as pa

CALENDAR_DATES_SPEC: dict[str, Any] = {
    'service_id': pa.string(),
    'date': pa.timestamp('s'),
    'exception_type': pa.uint8(),
}
CALENDAR_DATES_DEFAULTS: dict[str, Any] | None = None
CALENDAR_DATES_BOOLEAN_COLS: dict[str, tuple[Any, Any, Any]] | None = None
