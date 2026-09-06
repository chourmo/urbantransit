from typing import Any

import pyarrow as pa

STOPS_SPEC: dict[str, Any] = {
    'stop_id': pa.string(),
    'stop_code': pa.string(),
    'stop_name': pa.string(),
    'stop_desc': pa.string(),
    'stop_lat': pa.float64(),
    'stop_lon': pa.float64(),
    'zone_id': pa.string(),
    'stop_url': pa.string(),
    'location_type': pa.uint8(),
    'parent_station': pa.string(),
    'stop_timezone': pa.string(),
    'wheelchair_boarding': pa.uint8(),
    'level_id': pa.string(),
    'platform_code': pa.string(),
}
STOPS_DEFAULTS: dict[str, Any] | None = {
    'location_type': 0,
    'wheelchair_boarding': 0,
}
STOPS_BOOLEAN_COLS: dict[str, tuple[Any, Any, Any]] | None = {
    'wheelchair_boarding': (1, 2, 0)
}
