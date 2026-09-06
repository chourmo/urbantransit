from typing import Any

import pyarrow as pa

ROUTES_SPEC: dict[str, Any] = {
    'route_id': pa.string(),
    'agency_id': pa.string(),
    'route_short_name': pa.string(),
    'route_long_name': pa.string(),
    'route_desc': pa.string(),
    'route_type': pa.uint8(),
    'route_url': pa.string(),
    'route_color': pa.string(),
    'route_text_color': pa.string(),
    'route_sort_order': pa.uint8(),
    'continuous_pickup': pa.uint8(),
    'continuous_drop_off': pa.uint8(),
}
ROUTES_DEFAULTS: dict[str, Any] | None = {
    'agency_id': 1,
    'continuous_pickup': 0,
    'continuous_drop_off': 0,
    'route_sort_order': 0,
}
ROUTES_BOOLEAN_COLS: dict[str, tuple[Any, Any, Any]] | None = None
