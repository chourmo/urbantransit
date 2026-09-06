import itertools
from typing import TYPE_CHECKING

import geopandas as gpd
import pandas as pd
import shapely

from urbantransit.constants import LAST_STOP
from urbantransit.utils.logging import transitlog
from urbantransit.utils.time import DayTime

if TYPE_CHECKING:
    from urbantransit.transit import Lines, Transfers


class TransitGraph:
    def __init__(self, lines: "Lines", transfers: "Transfers", crs: str):
        self.crs = crs
        self.lines = lines
        self.transfers = transfers

        # data used for queries
        self.line_cache = None
        self.nodes = None
        self.edges = None
        self.search_results = {}

    def make_line_cache(self, lines: "Lines") -> dict:
        """return the maximum stop_sequence in lines under 65535"""
        df = lines.data.copy()
        df = df.loc[df.stop_sequence < LAST_STOP]
        df = df.groupby("line_gid")["stop_sequence"].max()
        return df.to_dict()

    def make_nodes(self, stops: pd.DataFrame) -> pd.DataFrame:
        """make nodes"""
        cols = [
            "stop_gid",
            "stop_name",
            "route_gid",
            "agency_gid",
            "time",
            "geometry",
            "line_gid",
            "stop_sequence",
        ]
        df = (
            stops[cols]
            .sort_values(["line_gid", "stop_sequence"])
            .set_index(["line_gid", "stop_sequence"])
            .copy()
        )
        df = df.set_geometry("geometry", crs=stops.crs).to_crs(self.crs)
        return df

    def make_edges(self, transfers: pd.DataFrame) -> dict:
        """Init transfer edges from transfers dataframe
        origin -> destination -> origin arrival time, next transfer index"""

        edges = {}

        cols = [
            "line_gid",
            "stop_sequence",
            "to_line_gid",
            "to_stop_sequence",
            "arrival",
            "to_departure",
            "to_timepoint",
        ]
        for row in transfers[cols].itertuples(name=None):
            source = (row[1], row[2])
            target = (row[3], row[4])
            if source not in edges:  # Check if the node is already added
                edges[source] = {}  # If not, create the node
            edges[source][target] = [row[6], row[7]]  # add a connection to its neighbor

        return edges

    # ----------------------------------------------------------------
    # graph traversal

    def sources(
        self, x: float, y: float, start: DayTime, distance: int, speed: float
    ) -> list:
        """Find source nodes and timepoints for a starting point
        returns a list of tuples (node id, timepoint)"""

        P = shapely.Point(x, y)
        ids = self.nodes.sindex.query(P, predicate="dwithin", distance=distance)
        sources = self.nodes.iloc[ids].copy()
        sources["walk"] = sources.geometry.distance(P) / speed
        sources = sources.explode("time").reset_index()

        # find timepoint index of each departure
        sources["timepoint"] = sources.groupby(["line_gid", "stop_sequence"]).cumcount()

        # filter first time
        sources = sources.loc[sources.time >= sources.walk + start.to_seconds()]
        sources = sources.drop_duplicates(["line_gid", "stop_sequence"], keep="first")

        sources = sources.set_index(["line_gid", "stop_sequence"])["timepoint"]
        return list(sources.to_dict().items())

    def format_results(self, start: DayTime, max_arrival: int) -> gpd.GeoDataFrame:
        """convert results to dataframe with line_gid and stop_sequence index, transfer, time and geometry columns"""

        df = pd.Series(self.search_results)
        df = pd.DataFrame(
            df.to_list(), columns=["timepoint", "transfers"], index=df.index
        )
        df.index.names = ["line_gid", "stop_sequence"]

        df = pd.merge(
            df,
            self.nodes,
            left_index=True,
            right_index=True,
            how="left",
        )

        df["time"] = df["time"].listarray.get(df["timepoint"])

        # drop if arrival after max_arrival
        if max_arrival is not None:
            df = df.loc[df["time"] <= max_arrival]

        df["time"] = df["time"] - start.to_seconds()
        self.search_results = {}

        return df.set_geometry("geometry", crs=self.crs).drop(columns=["timepoint"])

    def parse_transfer(self, target, target_values, timepoint, end_time):
        """Visit transfers and create a list of lines to visit next"""

        line_gid, stop_sequence = target
        departure, indices = target_values

        # impossible transfer as out of timetable
        if timepoint >= len(indices):
            return None

        # impossible transfer as out of max arrival time for search
        target_pos = int(indices[timepoint])
        if departure[target_pos] > end_time:
            return None

        return (line_gid, stop_sequence), target_pos

    def is_node_in_path(self, line_gid, index, timepoint, transfer_round):
        """return True if node is already in path with better or equal timepoint"""

        value = self.search_results.get((line_gid, index), None)
        if value is None:
            return False

        prev_tpoint, prev_round = value
        if prev_round < transfer_round:
            return True
        return (prev_round == transfer_round) and (prev_tpoint <= timepoint)

    def parse_line(self, line, transfer_round, end_time):
        """Visit lines and create a list of transfers to visit next"""

        # unpack line data
        line_id, start = line[0]
        timepoint = line[1]

        results = []

        # iterate over line stop_sequence from stop to end
        stop_sequences = list(range(start, self.line_cache[line_id] + 1))
        stop_sequences.append(LAST_STOP)

        for index in stop_sequences:
            # node is already traversed
            if self.is_node_in_path(line_id, index, timepoint, transfer_round):
                break

            # add this node to results
            self.search_results[(line_id, index)] = (timepoint, transfer_round)

            # do not look for transfers for first point to use at least one arc on a line
            if index == start:
                continue

            # find lines transfers from this node
            if (line_id, index) in self.edges:
                for target, target_value in self.edges[(line_id, index)].items():
                    new_line = self.parse_transfer(
                        target, target_value, timepoint, end_time
                    )
                    if new_line is not None:
                        results.append(new_line)

        return results

    def _shortest_distance(self, sources, max_transfers, end_time):
        # iterate for each transfer round
        for t in range(max_transfers + 1):
            # parse lines independtly and chain their transfers
            transfers = [self.parse_line(line, t, end_time) for line in sources]
            sources = list(itertools.chain(*transfers))
            transitlog.info(f"transfer round {t} : {len(sources)} transfers")

    def shortest_distance(
        self,
        x: float,
        y: float,
        start: DayTime,
        max_travel: int,
        start_distance: int,
        speed: float = 4.0,
        max_transfers: int = 4,
    ) -> gpd.GeoDataFrame:
        """Compute the shortest reachable path from a start point.

        Parameters
        ----------
        x : float
            X coordinate of the departure point.
        y : float
            Y coordinate of the departure point.
        start : DayTime
            Departure time.
        max_travel : int
            Maximum trip duration in seconds from the start time.
        start_distance : int
            Maximum walking distance from the origin point to reach the network.
        speed : float, default=4.0
            Walking speed used to translate the walking distance into time.
        max_transfers : int, default=4
            Maximum number of transfers allowed during the journey.

        Returns
        -------
        geopandas.GeoDataFrame
            Reachable destinations with geometry, arrival time, and transfer
            information.

        Notes
        -----
        This method builds the relevant subgraph for the requested time window,
        scans valid source nodes near the starting location, and evaluates the
        best itinerary under the transfer limit.
        """

        # Initialize the values of all nodes

        filtered = self.lines.filter_time_range(
            start, start.shift(max_travel), inclusive="both"
        )
        self.line_cache = self.make_line_cache(filtered)
        self.nodes = self.make_nodes(filtered.line_stops(geometry=True))
        self.edges = self.make_edges(self.transfers.expand_transfers(filtered.data))

        sources = self.sources(x, y, start, start_distance, speed)

        self.search_results = {}  # node:(timepoint, transfer round) with lowest timepoint

        self._shortest_distance(sources, max_transfers, max_travel)

        results = self.format_results(start, max_travel)

        # format results into dataframe
        self.line_cache = None
        self.nodes = None
        self.edges = None

        return results

    def shortest_path(self, source: str, target: str):
        raise NotImplementedError
