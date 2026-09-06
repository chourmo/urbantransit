from typing import Any

import pyarrow as pa

CALENDAR_SPEC: dict[str, Any] = {
    'service_id': pa.string(),
    'monday': pa.bool_(),
    'tuesday': pa.bool_(),
    'wednesday': pa.bool_(),
    'thursday': pa.bool_(),
    'friday': pa.bool_(),
    'saturday': pa.bool_(),
    'sunday': pa.bool_(),
    'start_date': pa.timestamp('s'),
    'end_date': pa.timestamp('s'),
}
CALENDAR_DEFAULTS: dict[str, Any] | None = None
CALENDAR_BOOLEAN_COLS: dict[str, tuple[Any, Any, Any]] | None = None
