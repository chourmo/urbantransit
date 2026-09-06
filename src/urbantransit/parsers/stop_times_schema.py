from typing import Any

import pyarrow as pa

STOP_TIMES_SPEC: dict[str, Any] = {
    'trip_id': pa.string(),
    'arrival_time': pa.string(),
    'departure_time': pa.string(),
    'stop_id': pa.string(),
    'stop_sequence': pa.uint16(),
    'stop_headsign': pa.string(),
    'pickup_type': pa.uint8(),
    'drop_off_type': pa.uint8(),
    'continuous_pickup': pa.uint8(),
    'continuous_drop_off': pa.uint8(),
    'shape_dist_traveled': pa.float32(),
    'timepoint': pa.bool_(),
}
STOP_TIMES_DEFAULTS: dict[str, Any] | None = None
STOP_TIMES_BOOLEAN_COLS: dict[str, tuple[Any, Any, Any]] | None = None
