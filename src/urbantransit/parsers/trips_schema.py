from typing import Any

import pyarrow as pa

TRIPS_SPEC: dict[str, Any] = {
    'route_id': pa.string(),
    'service_id': pa.string(),
    'trip_id': pa.string(),
    'trip_headsign': pa.string(),
    'trip_short_name': pa.string(),
    'direction_id': pa.uint8(),
    'block_id': pa.string(),
    'shape_id': pa.string(),
    'wheelchair_accessible': pa.uint8(),
    'bikes_allowed': pa.uint8(),
    'cars_allowed': pa.uint8(),
}
TRIPS_DEFAULTS: dict[str, Any] | None = {
    'trip_headsign': None,
    'trip_short_name': None,
    'direction_id': None,
    'block_id': None,
    'shape_id': None,
    'wheelchair_accessible': 0,
    'bikes_allowed': 0,
}
TRIPS_BOOLEAN_COLS: dict[str, tuple[Any, Any, Any]] | None = {
    'bikes_allowed': (1, 2, 0),
    'wheelchair_accessible': (1, 2, 0),
    'cars_allowed': (1, 2, 0),
}
