from typing import Any

import pyarrow as pa

AGENCY_SPEC: dict[str, Any] = {
    'agency_id': pa.string(),
    'agency_name': pa.string(),
    'agency_url': pa.string(),
    'agency_timezone': pa.string(),
    'agency_lang': pa.string(),
    'agency_phone': pa.string(),
    'agency_fare_url': pa.string(),
    'agency_email': pa.string(),
}
AGENCY_DEFAULTS: dict[str, Any] | None = {'agency_id': 1}
AGENCY_BOOLEAN_COLS: dict[str, tuple[Any, Any, Any]] | None = None
