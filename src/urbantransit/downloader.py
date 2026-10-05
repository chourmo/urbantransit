# Download resources from transport.data.gouv.fr
import asyncio
import json
import re
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from .utils.logging import transitlog

API_URL = "https://transport.data.gouv.fr/api"

RESOURCE_COLUMNS = [
    "dataset_id",
    "dataset_title",
    "dataset_slug",
    "dataset_type",
    "resource_id",
    "title",
    "format",
    "type",
    "updated",
    "is_available",
    "url",
]


def _norm(text: str) -> str:
    """Lowercase and strip accents for tolerant name comparisons."""
    text = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in text if not unicodedata.combining(c)).lower().strip()


class TransportDataGouv:
    """Download resources from https://transport.data.gouv.fr.

    Uses the public API (https://transport.data.gouv.fr/swaggerui) to list
    datasets and resources, optionally restricted to the datasets of an AOM
    (Autorité Organisatrice de la Mobilité), and downloads them to a folder.

    Parameters
    ----------
    folder : Path or string
        Destination folder for downloaded files, created if needed.
    timeout : float
        Network timeout in seconds.

    Examples
    --------
    >>> api = TransportDataGouv("data")
    >>> api.download(aom="Île-de-France Mobilités", format="GTFS")
    >>> api.download(region="Bretagne", format="GTFS")
    >>> await api.download_async(region="Bretagne", format="GTFS")  # in async code
    >>> api.download(aom="287500078", format="GTFS", title="métro")
    """

    def __init__(self, folder=".", timeout: float = 60):
        self.folder = Path(folder)
        self.timeout = timeout
        self._datasets: dict[str, list[dict]] = {}
        self._aoms: list[dict] | None = None

    # ------------------------------------------------------------------ API
    def _get(self, url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "urbantransit"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read()

    def _get_json(self, path: str, **params):
        url = f"{API_URL}/{path.lstrip('/')}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return json.loads(self._get(url))

    def datasets(self, refresh: bool = False) -> list[dict]:
        """All datasets published on the portal (cached)."""
        if refresh or "all" not in self._datasets:
            self._datasets["all"] = self._get_json("datasets")
        return self._datasets["all"]

    def aoms(self, refresh: bool = False) -> pd.DataFrame:
        """All AOMs with name, siren, main commune INSEE code (cached)."""
        if refresh or self._aoms is None:
            data = self._get_json("aoms/geojson")
            self._aoms = [f["properties"] for f in data["features"]]
        return pd.DataFrame(self._aoms)

    def find_aom(self, aom: str) -> dict:
        """Find an AOM by SIREN, main commune INSEE code or (part of) its name.

        Raises ValueError if no AOM or several AOMs match.
        """
        aoms = self.aoms()
        key = str(aom).strip()
        exact = aoms[(aoms["siren"] == key) | (aoms["insee_commune_principale"] == key)]
        if len(exact) == 1:
            return exact.iloc[0].to_dict()

        names = aoms["nom"].map(_norm)
        k = _norm(key)
        found = aoms[names == k]
        if found.empty:
            found = aoms[names.str.contains(re.escape(k))]
        if found.empty:
            raise ValueError(f"No AOM matching {aom!r}")
        if len(found) > 1:
            options = ", ".join(found["nom"].head(10))
            raise ValueError(f"Several AOMs match {aom!r}: {options}")
        return found.iloc[0].to_dict()

    # --------------------------------------------------------------- search
    @staticmethod
    def _belongs_to_aom(dataset: dict, aom: dict) -> bool:
        siren = aom["siren"]
        if any(o.get("siren") == siren for o in dataset.get("legal_owners") or []):
            return True
        for area in dataset.get("covered_area") or []:
            if area.get("type") == "epci" and area.get("insee") == siren:
                return True
            if (
                area.get("type") == "commune"
                and area.get("insee") == aom["insee_commune_principale"]
            ):
                return True
        return False

    @staticmethod
    def _in_region(dataset: dict, region: str) -> bool:
        key = _norm(region)
        return any(
            a.get("type") == "region"
            and key in (_norm(a.get("insee", "")), _norm(a.get("nom", "")))
            for a in dataset.get("covered_area") or []
        )

    def search(
        self,
        aom: str | None = None,
        region: str | None = None,
        type: str | None = "public-transit",
        format: str | None = None,
        title: str | None = None,
        dataset: str | None = None,
        resource_type: str | None = None,
        available_only: bool = True,
    ) -> pd.DataFrame:
        """List resources matching the criteria, one row per resource.

        Parameters
        ----------
        aom : str, optional
            AOM SIREN, main commune INSEE code or name. Keeps the datasets
            owned by the AOM or covering its EPCI / main commune.
        region : str, optional
            Region name or INSEE code (e.g. "Bretagne" or "53"). Keeps the
            datasets whose covered area includes this region.
        type : str, optional
            Dataset type, e.g. "public-transit", "bike-data" (None for all).
        format : str, optional
            Resource format, e.g. "GTFS", "NeTEx", "gtfs-rt" (case insensitive).
        title : str, optional
            Text contained in the resource title (case/accent insensitive).
        dataset : str, optional
            Dataset id, slug, or text contained in the dataset title.
        resource_type : str, optional
            Resource type, e.g. "main", "documentation", "other".
        available_only : bool
            Only keep resources flagged as available.
        """
        aom_info = self.find_aom(aom) if aom is not None else None
        if aom_info is not None:
            transitlog.info("AOM: %s (%s)", aom_info["nom"], aom_info["siren"])

        rows = []
        for ds in self.datasets():
            if type and ds.get("type") != type:
                continue
            if aom_info is not None and not self._belongs_to_aom(ds, aom_info):
                continue
            if region and not self._in_region(ds, region):
                continue
            if (
                dataset
                and dataset not in (ds.get("id"), ds.get("slug"))
                and _norm(dataset) not in _norm(ds.get("title", ""))
            ):
                continue
            for r in ds.get("resources") or []:
                if format and _norm(r.get("format", "")) != _norm(format):
                    continue
                if title and _norm(title) not in _norm(r.get("title", "")):
                    continue
                if resource_type and r.get("type") != resource_type:
                    continue
                if available_only and not r.get("is_available", True):
                    continue
                rows.append(
                    {
                        "dataset_id": ds.get("id"),
                        "dataset_title": ds.get("title"),
                        "dataset_slug": ds.get("slug"),
                        "dataset_type": ds.get("type"),
                        "resource_id": r.get("id"),
                        "title": r.get("title"),
                        "format": r.get("format"),
                        "type": r.get("type"),
                        "updated": r.get("updated"),
                        "is_available": r.get("is_available"),
                        "url": r.get("url") or r.get("original_url"),
                    }
                )
        return pd.DataFrame(rows, columns=RESOURCE_COLUMNS)

    # ------------------------------------------------------------- download
    @staticmethod
    def _filename(row) -> str:
        fmt = str(row["format"] or "").lower()
        suffix = Path(urllib.parse.urlparse(str(row["url"])).path).suffix.lower()
        if fmt in ("gtfs", "netex", "gtfs-rt", "gbfs") or not suffix:
            suffix = (
                ".zip" if fmt in ("gtfs", "netex") else (suffix or f".{fmt or 'bin'}")
            )
        slug = re.sub(r"[^\w-]+", "_", str(row["dataset_slug"] or row["dataset_id"]))
        return f"{slug}_{row['resource_id']}{suffix}"

    def _fetch_to(self, url: str, path: Path) -> None:
        tmp = path.with_name(path.name + ".part")
        tmp.write_bytes(self._get(url))
        tmp.replace(path)

    async def download_async(
        self,
        resources: pd.DataFrame | None = None,
        overwrite: bool = False,
        max_concurrent: int = 5,
        **criteria,
    ) -> list[Path]:
        """Download resources concurrently and return the file paths.

        Parameters
        ----------
        resources : DataFrame, optional
            Result of `search` (possibly further filtered). If None, `search`
            is called with `criteria`.
        overwrite : bool
            Re-download files that already exist.
        max_concurrent : int
            Maximum number of simultaneous downloads.
        **criteria
            Arguments of `search` (aom, region, type, format, title, ...).
        """
        if resources is None:
            resources = await asyncio.to_thread(self.search, **criteria)
        elif criteria:
            raise ValueError("Pass either resources or search criteria, not both")

        self.folder.mkdir(parents=True, exist_ok=True)
        sem = asyncio.Semaphore(max(1, max_concurrent))

        async def fetch(row) -> Path:
            path = self.folder / self._filename(row)
            if path.exists() and not overwrite:
                transitlog.info("Skipping existing %s", path.name)
                return path
            async with sem:
                transitlog.info("Downloading %s", row["url"])
                await asyncio.to_thread(self._fetch_to, row["url"], path)
            return path

        rows = [row for _, row in resources.iterrows()]
        return list(await asyncio.gather(*(fetch(r) for r in rows)))

    def download(self, *args, **kwargs) -> list[Path]:
        """Blocking wrapper around `download_async` (same arguments).

        Safe to call from a running event loop (e.g. Jupyter); in async code
        prefer awaiting `download_async`.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.download_async(*args, **kwargs))
        with ThreadPoolExecutor(1) as pool:
            return pool.submit(
                asyncio.run, self.download_async(*args, **kwargs)
            ).result()
