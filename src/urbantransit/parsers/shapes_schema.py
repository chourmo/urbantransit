from typing import Any

import pyarrow as pa

SHAPES_SPEC: dict[str, Any] = {
    'shape_id': pa.string(),
    'shape_pt_lat': pa.float64(),
    'shape_pt_lon': pa.float64(),
    'shape_pt_sequence': pa.uint32(),
    'shape_dist_traveled': pa.float32(),
}
SHAPES_DEFAULTS: dict[str, Any] | None = None
SHAPES_BOOLEAN_COLS: dict[str, tuple[Any, Any, Any]] | None = None
