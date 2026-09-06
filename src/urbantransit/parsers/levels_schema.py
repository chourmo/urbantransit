from typing import Any

import pyarrow as pa

LEVELS_SPEC: dict[str, Any] = {
    'level_id': pa.string(),
    'level_index': pa.int16(),
    'level_name': pa.string(),
}
LEVELS_DEFAULTS: dict[str, Any] | None = None
LEVELS_BOOLEAN_COLS: dict[str, tuple[Any, Any, Any]] | None = None
