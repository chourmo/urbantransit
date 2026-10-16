"""Utilities for storing UrbanTransit metadata in dataframe and Parquet attributes."""

import json

import pandas as pd
import pyarrow as pa

METADATA_KEY = b"urbantransit"
DATAFRAME_METADATA_KEY = "urbantransit"
PANDAS_ATTRS_KEY = b"PANDAS_ATTRS"
METADATA_VERSION = 1


def add_metadata(data, year: int, week: int) -> pd.DataFrame:
    """Add UrbanTransit metadata to dataframe attributes."""
    res = data.copy()
    res.attrs[DATAFRAME_METADATA_KEY] = {
        "metadata_version": METADATA_VERSION,
        "year": year,
        "week": week,
    }
    return res


def read_metadata(
    schema: pa.Schema, year: int | None = None, week: int | None = None
) -> tuple[int, int]:
    """Read and validate UrbanTransit year and week from a Parquet schema."""
    metadata = schema.metadata or {}
    value = metadata.get(METADATA_KEY)
    if value is None:
        pandas_attrs = metadata.get(PANDAS_ATTRS_KEY) or metadata.get(b"pandas")
        if pandas_attrs is not None:
            try:
                parsed_pandas_attrs = json.loads(pandas_attrs)
                value = parsed_pandas_attrs.get(
                    DATAFRAME_METADATA_KEY,
                    parsed_pandas_attrs.get("attributes", {}).get(
                        DATAFRAME_METADATA_KEY
                    ),
                )
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                TypeError,
            ) as error:
                raise ValueError(
                    "Parquet file has invalid UrbanTransit metadata"
                ) from error
    if value is None:
        raise ValueError("Parquet file does not contain UrbanTransit metadata")

    try:
        parsed = json.loads(value) if isinstance(value, bytes) else value
        version = parsed["metadata_version"]
        metadata_year = parsed["year"]
        metadata_week = parsed["week"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError("Parquet file has invalid UrbanTransit metadata") from error

    if version != METADATA_VERSION:
        raise ValueError(
            f"Unsupported UrbanTransit metadata version {version}; "
            f"expected {METADATA_VERSION}"
        )
    if not isinstance(metadata_year, int) or isinstance(metadata_year, bool):
        raise TypeError("Parquet UrbanTransit metadata has an invalid year")
    if not isinstance(metadata_week, int) or isinstance(metadata_week, bool):
        raise TypeError("Parquet UrbanTransit metadata has an invalid week")
    if year is not None and year != metadata_year:
        raise ValueError(
            f"Requested year {year} does not match Parquet metadata year {metadata_year}"
        )
    if week is not None and week != metadata_week:
        raise ValueError(
            f"Requested week {week} does not match Parquet metadata week {metadata_week}"
        )

    return metadata_year, metadata_week
