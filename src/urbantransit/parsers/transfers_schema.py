from typing import Any

import pyarrow as pa

TRANSFERS_SPEC: dict[str, Any] = {
    'from_stop_id': pa.string(),
    'to_stop_id': pa.string(),
    'from_route_id': pa.string(),
    'to_route_id': pa.string(),
    'from_trip_id': pa.string(),
    'to_trip_id': pa.string(),
    'transfer_type': pa.uint8(),
    'min_transfer_time': pa.uint16(),
}
TRANSFERS_DEFAULTS: dict[str, Any] | None = None
TRANSFERS_BOOLEAN_COLS: dict[str, tuple[Any, Any, Any]] | None = None
