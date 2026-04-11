from typing import Optional

import numpy as np
import pandas as pd
import pyarrow as pa


def random_ids(
    size: int,
    exclude=None,
    low: int = 0,
    high: int = 9_000_000_000_000_000,
    seed: int | None = None,
    multiplier: float = 1.5,
) -> np.ndarray:
    """Generate unique random int64 values, excluding provided values."""
    if size < 0:
        raise ValueError("size must be >= 0")
    if size == 0:
        return np.array([], dtype=np.int64)
    if low >= high:
        raise ValueError("low must be < high")

    excluded = (
        np.array([], dtype=np.int64)
        if exclude is None
        else np.asarray(exclude, dtype=np.int64).ravel()
    )
    excluded = excluded[(excluded >= low) & (excluded < high)]
    excluded = np.unique(excluded)

    available = (high - low) - excluded.size
    if available < size:
        raise ValueError("Not enough available integers in range after exclusions")

    rng = np.random.default_rng(seed)

    # create random integers of 1.5 * (size + excluded) to ensure we have enough unique values after exclusions
    random_ints = rng.integers(
        low, high, int(multiplier * (size + excluded.size)), dtype=np.int64
    )

    # create
    random_ints = np.unique(random_ints)
    random_ints = np.delete(random_ints, np.where(np.isin(random_ints, excluded)))

    # select unique random integers of the requested size
    if random_ints.size < size:
        random_ints = random_ints(
            size,
            exclude=exclude,
            low=low,
            high=high,
            seed=seed,
            multiplier=multiplier * 2,
        )
        raise ValueError("Not enough unique integers generated after exclusions")
    return pa.array(rng.choice(random_ints, size=size, replace=False))


def hash_ids(
    df: pd.DataFrame | pd.Series,
    columns: list[str],
    sep: str = "_",
    dtype: str = "uint64[pyarrow]",
) -> pd.Series:
    """Create a unique id of a Series or a Dataframe columns."""

    gid = df[columns].T.agg(sep.join)
    return pd.util.hash_pandas_object(gid, index=False).astype(dtype)


def intersect_ids(
    df1: pd.Series,
    df2: pd.Series,
    left_id: Optional[str] = None,
    right_id: Optional[str] = None,
    drop_left: bool = True,
    drop_right: bool = True,
) -> tuple[pd.Series, pd.Series]:
    """
    Return the subset of df1 and df2 where values in left_id and right_id columns both exists

    Arguments :
        df1, df2 :
            left and right dataframes
        left_id, right_id:
            left and right column names containing the ids, if None, use index
        drop_left, drop_right: boolean, default True:
            if True, drop the rows, else replace id values by NA

    Returns :
        two new dataframes
    """

    def _input(df, id):
        if id is None:
            return df.index.drop_duplicates()
        elif id in df.columns:
            return pd.Index(df[id].drop_duplicates())
        else:
            raise ValueError(f"{id} must be in columns")

    def _results(df, subset, id, drop):
        if drop and id is None:
            return df.loc[df.index.isin(subset)].copy()
        elif drop:
            return df.loc[df[id].isin(subset)].copy()
        elif id is None:
            res = df.copy()
            res.loc[(res.index.isna()) | (~res.index.isin(subset)), id] = pd.NA
            return res
        else:
            res = df.copy()
            res.loc[(res[id].isna()) | (~res[id].isin(subset)), id] = pd.NA
            return res

    uniques_left = _input(df1, left_id)
    uniques_right = _input(df2, right_id)

    subset = uniques_left.intersection(uniques_right)

    lenLeft, lenRight = len(uniques_left), len(uniques_right)
    if (lenLeft == lenRight) and (lenLeft == len(subset)):
        return df1, df2

    left = _results(df1, subset, left_id, drop_left)
    right = _results(df2, subset, right_id, drop_right)

    return left, right


def renumber_ids(ids, to_renumber):
    """Return a mapper Series with new ids for to_renumber values."""

    if isinstance(ids, pd.Series):
        values = ids.loc[ids.isin(to_renumber)].drop_duplicates()
    elif isinstance(ids, pd.Index):
        values = ids[ids.isin(to_renumber)].drop_duplicates()
    else:
        values = pd.Series(ids).loc[pd.Series(ids).isin(to_renumber)].drop_duplicates()

    if len(values) == 0:
        return values

    new_values = random_ids(size=len(values), exclude=values)

    return pd.Series(new_values, index=values.values, dtype=ids.dtype)


def filter_ids(df, gids):
    """Filter dataframe to only include gids"""
    length = len(df)

    if length == 0:
        raise ValueError("No gids found in data")

    return df.loc[df.index.isin(gids)].copy()
