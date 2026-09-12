import marimo

__generated_with = "0.24.0"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import numpy as np
    import pandas as pd

    from urbantransit.parsers.gtfs_to_parquet import GTFStoParquet

    return GTFStoParquet, mo, np, pd


@app.cell
def _(mo):
    mo.md("""
    # `get_line_id` playground

    Compares the hash-based `get_line_id` (new) against the position-signature
    `get_line_id_old`, on a small synthetic `sequence` DataFrame covering:

    - two trips sharing the same stop pattern that do **not** overlap in time
      (must share a `line_gid`)
    - a trip with the **reversed** stop pattern (must get a different `line_gid`)
    - a trip on a **different route** with the same stop pattern (merges unless
      `split_by_route=True`)
    - two trips with the same stop pattern that **overlap** in time, i.e. one
      "overtakes" the other (must be split into different `line_gid`s)

    `get_line_id` resolves overlapping schedules with a `conflict_strategy`:

    - `"min_groups"` (default): single-pass, uses the *exact* per-stop
      compatibility test and minimizes the number of resulting `line_gid`
      groups - important for shortest-path search, where fewer lines/edges
      is preferable.
    - `"iterative"`: the original repeated re-sort/re-hash-until-convergence
      approach; also minimal-ish but slower and can end up with more groups
      than `"min_groups"`.
    """)
    return


@app.cell
def _(pd):
    def add_trip(rows, seq_id, route_gid, stops, deps, arrs, direction_id=0):
        """Append one row per stop for a trip to `rows` (in place)."""
        for i, (stop_gid, departure, arrival) in enumerate(zip(stops, deps, arrs)):
            rows.append(
                dict(
                    seq_id=seq_id,
                    route_gid=route_gid,
                    direction_id=direction_id,
                    stop_gid=stop_gid,
                    stop_sequence=i,
                    departure=departure,
                    arrival=arrival,
                )
            )

    def make_sequence(rows) -> pd.DataFrame:
        """Build a `sequence` DataFrame with the dtypes expected by get_line_id."""
        df = pd.DataFrame(rows)
        df["seq_id"] = df["seq_id"].astype("int64[pyarrow]")
        df["stop_gid"] = df["stop_gid"].astype("int64[pyarrow]")
        df["route_gid"] = df["route_gid"].astype("int64[pyarrow]")
        df["stop_sequence"] = df["stop_sequence"].astype("uint16[pyarrow]")
        # signed dtype: arrival/departure subtraction must be able to go negative
        # to detect schedule conflicts (see gtfs_to_parquet.to_seconds)
        df["departure"] = df["departure"].astype("int32[pyarrow]")
        df["arrival"] = df["arrival"].astype("int32[pyarrow]")
        return df

    return add_trip, make_sequence


@app.cell
def _(add_trip, make_sequence):
    rows = []

    # trip 0 and trip 1: same stop pattern, non-overlapping times -> same line_gid
    add_trip(rows, seq_id=0, route_gid=1, stops=[1, 2, 3], deps=[0, 100, 200], arrs=[0, 100, 200])
    add_trip(rows, seq_id=1, route_gid=1, stops=[1, 2, 3], deps=[300, 400, 500], arrs=[300, 400, 500])

    # trip 2: reversed stop pattern -> different line_gid (order-sensitive hash)
    add_trip(rows, seq_id=2, route_gid=1, stops=[3, 2, 1], deps=[0, 100, 200], arrs=[0, 100, 200])

    # trip 3: same pattern as trip 0/1, different route -> merges unless split_by_route
    add_trip(rows, seq_id=3, route_gid=2, stops=[1, 2, 3], deps=[600, 700, 800], arrs=[600, 700, 800])

    # trip 4: same pattern as trip 0, but overlaps it (departs stop 1 before trip 0
    # arrives at stop 3) -> must be split into a different line_gid
    add_trip(rows, seq_id=4, route_gid=1, stops=[1, 2, 3], deps=[10, 50, 90], arrs=[10, 50, 90])

    sequence = make_sequence(rows)
    sequence
    return (sequence,)


@app.cell
def _(mo):
    mo.md("""
    ## `get_line_id` (default: `conflict_strategy="min_groups"`)
    """)
    return


@app.cell
def _(GTFStoParquet, sequence):
    line_id_new = GTFStoParquet.get_line_id(sequence, name="demo-new").to_frame("line_gid")
    line_id_new
    return (line_id_new,)


@app.cell
def _(mo):
    mo.md("""
    ## `get_line_id` with `split_by_route=True`
    """)
    return


@app.cell
def _(GTFStoParquet, sequence):
    line_id_split = GTFStoParquet.get_line_id(
        sequence, name="demo-split-by-route", split_by_route=True
    ).to_frame("line_gid")
    line_id_split
    return (line_id_split,)


@app.cell
def _(mo):
    mo.md("""
    ## `get_line_id` with `conflict_strategy="iterative"`
    """)
    return


@app.cell
def _(GTFStoParquet, sequence):
    line_id_iterative = GTFStoParquet.get_line_id(
        sequence, name="demo-iterative", conflict_strategy="iterative"
    ).to_frame("line_gid")
    line_id_iterative
    return (line_id_iterative,)


@app.cell
def _(mo):
    mo.md("""
    ## `get_line_id_old` (position-signature, kept for comparison)
    """)
    return


@app.cell
def _(GTFStoParquet, sequence):
    line_id_old = GTFStoParquet.get_line_id_old(sequence, name="demo-old").to_frame("line_gid")
    line_id_old
    return (line_id_old,)


@app.cell
def _(mo):
    mo.md("""
    ## Side-by-side comparison

    `same_group` compares, for each pair of seq_id, whether they end up in the
    same `line_gid` group consistently across implementations.
    """)
    return


@app.cell
def _(line_id_iterative, line_id_new, line_id_old, line_id_split, pd):
    comparison = pd.DataFrame(
        {
            "min_groups": line_id_new["line_gid"],
            "iterative": line_id_iterative["line_gid"],
            "old": line_id_old["line_gid"],
            "split_by_route": line_id_split["line_gid"],
        }
    )
    # relabel each column with dense integer codes so grouping is easy to read,
    # regardless of each implementation's underlying id scheme (hash vs int)
    grouped = comparison.apply(lambda col: pd.factorize(col)[0])
    grouped
    return


@app.cell
def _(mo):
    mo.md("""
    # Benchmarks

    Random synthetic `sequence` DataFrames of increasing size, built from a
    pool of stop patterns shared across trips (so line_gid grouping is
    exercised), with random departure offsets so some trips overlap.
    """)
    return


@app.cell
def _(np, pd):
    def make_random_sequence(n_trips, n_patterns, n_routes, stops_per_trip=8, seed=0):
        rng = np.random.default_rng(seed)
        rows = []
        patterns = [
            rng.choice(np.arange(1, 500), size=stops_per_trip, replace=False)
            for _ in range(n_patterns)
        ]
        for seq_id in range(n_trips):
            pattern = patterns[rng.integers(0, n_patterns)]
            route_gid = int(rng.integers(0, n_routes))
            start = int(rng.integers(0, 24 * 3600))
            offsets = np.cumsum(rng.integers(30, 300, size=stops_per_trip))
            deps = start + offsets
            arrs = deps  # simplify: arrival == departure at each stop
            for i, (s, d, a) in enumerate(zip(pattern, deps, arrs)):
                rows.append((seq_id, route_gid, 0, int(s), i, int(d), int(a)))
        df = pd.DataFrame(
            rows,
            columns=[
                "seq_id",
                "route_gid",
                "direction_id",
                "stop_gid",
                "stop_sequence",
                "departure",
                "arrival",
            ],
        )
        df["seq_id"] = df["seq_id"].astype("int64[pyarrow]")
        df["stop_gid"] = df["stop_gid"].astype("int64[pyarrow]")
        df["route_gid"] = df["route_gid"].astype("int64[pyarrow]")
        df["stop_sequence"] = df["stop_sequence"].astype("uint16[pyarrow]")
        df["departure"] = df["departure"].astype("int32[pyarrow]")
        df["arrival"] = df["arrival"].astype("int32[pyarrow]")
        return df

    return (make_random_sequence,)


@app.cell
def _(GTFStoParquet, make_random_sequence, pd, time):
    def run_benchmark(sizes=(1_000, 5_000, 20_000)):
        records = []
        variants = [
            (
                "get_line_id(min_groups)",
                lambda s: GTFStoParquet.get_line_id(s, name="bench"),
            ),
            (
                "get_line_id(iterative)",
                lambda s: GTFStoParquet.get_line_id(
                    s, name="bench", conflict_strategy="iterative"
                ),
            ),
            (
                "get_line_id(split_by_route=True)",
                lambda s: GTFStoParquet.get_line_id(
                    s, name="bench", split_by_route=True
                ),
            ),
            ("get_line_id_old", lambda s: GTFStoParquet.get_line_id_old(s, name="bench")),
        ]
        for n_trips in sizes:
            seq = make_random_sequence(
                n_trips, n_patterns=max(5, n_trips // 200), n_routes=20
            )
            for label, fn in variants:
                start = time.perf_counter()
                result = fn(seq)
                elapsed = time.perf_counter() - start
                records.append(
                    {
                        "n_trips": n_trips,
                        "n_rows": len(seq),
                        "variant": label,
                        "seconds": round(elapsed, 4),
                        "unique_line_gid": result.nunique(),
                    }
                )
        return pd.DataFrame(records)

    return (run_benchmark,)


@app.cell
def _(run_benchmark):
    benchmark_results = run_benchmark()
    benchmark_results
    return


@app.cell
def _(mo):
    mo.md("""
    **Reading the results:** `get_line_id(min_groups)` (the default) uses the
    *exact* per-stop compatibility test (same definition as `iterative`/`old`)
    but assigns each trip, in a single pass, to the best still-open sub-line
    instead of repeatedly re-sorting/re-hashing until convergence. In practice
    it produces the **same or fewer** groups than `iterative`/`old` while
    running faster, especially on heavily overlapping schedules (see the
    heavy-overlap benchmark below) - which matters for shortest-path search,
    since fewer `line_gid` groups mean fewer distinct lines/edges to
    traverse.
    """)
    return


@app.cell
def _(mo):
    mo.md("""
    ## Heavy-overlap stress test

    Many trips sharing very few stop patterns, all departing within a short
    time window (heavy scheduling overlap) - the scenario where `iterative`'s
    repeated re-hashing rounds are most costly and `min_groups`'s single-pass
    chain assignment pays off the most.
    """)
    return


@app.cell
def _(np, pd):
    def make_heavy_overlap_sequence(n_trips, n_patterns=3, stops_per_trip=6, seed=1):
        rng = np.random.default_rng(seed)
        rows = []
        patterns = [
            rng.choice(np.arange(1, 50), size=stops_per_trip, replace=False)
            for _ in range(n_patterns)
        ]
        for seq_id in range(n_trips):
            pattern = patterns[rng.integers(0, n_patterns)]
            start = int(rng.integers(0, 3600))  # crammed into 1 hour -> heavy overlap
            offsets = np.cumsum(rng.integers(30, 120, size=stops_per_trip))
            deps = start + offsets
            for i, (s, d) in enumerate(zip(pattern, deps)):
                rows.append((seq_id, 1, 0, int(s), i, int(d), int(d)))
        df = pd.DataFrame(
            rows,
            columns=[
                "seq_id",
                "route_gid",
                "direction_id",
                "stop_gid",
                "stop_sequence",
                "departure",
                "arrival",
            ],
        )
        df["seq_id"] = df["seq_id"].astype("int64[pyarrow]")
        df["stop_gid"] = df["stop_gid"].astype("int64[pyarrow]")
        df["route_gid"] = df["route_gid"].astype("int64[pyarrow]")
        df["stop_sequence"] = df["stop_sequence"].astype("uint16[pyarrow]")
        df["departure"] = df["departure"].astype("int32[pyarrow]")
        df["arrival"] = df["arrival"].astype("int32[pyarrow]")
        return df

    return (make_heavy_overlap_sequence,)


@app.cell
def _(GTFStoParquet, make_heavy_overlap_sequence, pd, time):
    def run_heavy_overlap_benchmark(sizes=(1_000, 5_000, 10_000)):
        records = []
        variants = [
            (
                "min_groups",
                lambda s: GTFStoParquet.get_line_id(s, name="bench"),
            ),
            (
                "iterative",
                lambda s: GTFStoParquet.get_line_id(
                    s, name="bench", conflict_strategy="iterative"
                ),
            ),
        ]
        for n_trips in sizes:
            seq = make_heavy_overlap_sequence(n_trips)
            for label, fn in variants:
                start = time.perf_counter()
                result = fn(seq)
                elapsed = time.perf_counter() - start
                records.append(
                    {
                        "n_trips": n_trips,
                        "variant": label,
                        "seconds": round(elapsed, 4),
                        "unique_line_gid": result.nunique(),
                    }
                )
        return pd.DataFrame(records)

    return (run_heavy_overlap_benchmark,)


@app.cell
def _(run_heavy_overlap_benchmark):
    heavy_overlap_results = run_heavy_overlap_benchmark()
    heavy_overlap_results
    return


@app.cell
def _():
    import time

    return (time,)


if __name__ == "__main__":
    app.run()
