"""Offline native WorldCover 2021 v200 sampling with bounded block caching."""

from collections import Counter, OrderedDict, defaultdict
from pathlib import Path
import json, math
import numpy as np
import rasterio
from rasterio.windows import Window
from .elevation import sha256
from .geographic_confidence import LAND_CODES, RISK_CODES
from .geographic_geometry import longitude


def _tile(lat, lon):
    return (
        f'{"N" if lat>=0 else "S"}{abs(lat):02d}{"E" if lon>=0 else "W"}{abs(lon):03d}'
    )


def _snap(x):
    n = round(x)
    return float(n) if abs(x - n) < 1e-7 else x


class WorldCover:
    """Native blocks grouped per batch; 64-MiB LRU and bounded open handles."""

    def __init__(self, cache, limit=64 * 1024**2):
        self.cache = Path(cache)
        self.manifest = json.loads((self.cache / "manifest.json").read_text())
        if self.manifest["product"] != "ESA WorldCover 2021 v200":
            raise ValueError("Unexpected WorldCover revision")
        self.support = self.manifest.get("coverage", dict(south=-60, north=82.75))
        self.revision = sha256(self.cache / "manifest.json")
        self.limit = limit
        self.blocks = OrderedDict()
        self.bytes = 0
        self.handles = OrderedDict()
        self.io = Counter()
        for name, entry in self.manifest["tiles"].items():
            p = self.cache / entry["path"]
            if (
                not p.resolve().is_relative_to(self.cache.resolve())
                or not p.is_file()
                or p.stat().st_size != entry["size"]
                or sha256(p) != entry["sha256"]
            ):
                raise ValueError(f"WorldCover integrity error: {name}")

    def ds(self, tile):
        if tile not in self.manifest["tiles"]:
            raise ValueError(f"WorldCover regional cache incomplete: {tile}")
        if tile in self.handles:
            ds = self.handles.pop(tile)
            self.handles[tile] = ds
            return ds
        while len(self.handles) >= 4:
            self.handles.popitem(last=False)[1].close()
        ds = rasterio.open(self.cache / self.manifest["tiles"][tile]["path"])
        self.handles[tile] = ds
        if (
            str(ds.crs) != "EPSG:4326"
            or ds.count != 1
            or ds.nodata != 0
            or ds.transform.b
            or ds.transform.d
        ):
            ds.close()
            raise ValueError("Unsupported WorldCover native grid")
        return ds

    def block(self, tile, br, bc):
        k = (tile, br, bc)
        if k in self.blocks:
            a = self.blocks.pop(k)
            self.blocks[k] = a
            self.io["hits"] += 1
            return a
        ds = self.ds(tile)
        bh, bw = ds.block_shapes[0]
        a = ds.read(
            1,
            window=Window(
                bc * bw,
                br * bh,
                min(bw, ds.width - bc * bw),
                min(bh, ds.height - br * bh),
            ),
        )
        self.io["reads"] += 1
        while self.blocks and self.bytes + a.nbytes > self.limit:
            self.bytes -= self.blocks.popitem(last=False)[1].nbytes
        if a.nbytes <= self.limit:
            self.blocks[k] = a
            self.bytes += a.nbytes
        self.io["peak_cache_bytes"] = max(self.io["peak_cache_bytes"], self.bytes)
        return a

    def sample(self, items):
        """Each item has point and exact WBM support. Never use an overview."""
        requests = defaultdict(list)
        hist = [Counter() for _ in items]
        centre = [None] * len(items)
        supported = [True] * len(items)
        windows = [[] for _ in items]
        for i, item in enumerate(items):
            p = item["point"]
            west, south, east, north = item["wbm_support"]
            if south < self.support["south"] or north > self.support["north"]:
                supported[i] = False
                # Initial regions are covered; synthetic unsupported cases
                # exercise the policy, not invented polar raster labels.
                continue
            # Map rows run north-to-south: a centre exactly on a 3-degree
            # parallel belongs to the tile immediately south of that line.
            centre_lat = (math.ceil(p["lat"] / 3) - 1) * 3
            centre_lon = math.floor(longitude(p["lon"]) / 3) * 3
            centre_tile = _tile(centre_lat, centre_lon)
            centre_ds = self.ds(centre_tile)
            centre_row, centre_col = centre_ds.index(longitude(p["lon"]), p["lat"])
            if not (
                0 <= centre_row < centre_ds.height and 0 <= centre_col < centre_ds.width
            ):
                raise ValueError("Exact centre outside declared WorldCover grid")
            lat0 = math.floor(south / 3) * 3
            lat1 = math.floor(np.nextafter(north, -np.inf) / 3) * 3
            lon0 = math.floor(west / 3) * 3
            lon1 = math.floor(np.nextafter(east, -np.inf) / 3) * 3
            for lat in range(lat0, lat1 + 1, 3):
                for lon in range(lon0, lon1 + 1, 3):
                    tile = _tile(lat, int(longitude(lon)))
                    ds = self.ds(tile)
                    inv = ~ds.transform
                    shift = lon - int(longitude(lon))
                    x0, y0 = inv * (max(west, lon) - shift, min(north, lat + 3))
                    x1, y1 = inv * (min(east, lon + 3) - shift, max(south, lat))
                    c0 = max(0, int(math.floor(_snap(x0))))
                    r0 = max(0, int(math.floor(_snap(y0))))
                    c1 = min(ds.width, int(math.ceil(_snap(x1))))
                    r1 = min(ds.height, int(math.ceil(_snap(y1))))
                    if c1 <= c0 or r1 <= r0:
                        continue
                    bh, bw = ds.block_shapes[0]
                    windows[i].append(
                        dict(
                            tile=tile,
                            window=[c0, r0, c1 - c0, r1 - r0],
                            role="native_WBM_support",
                        )
                    )
                    cp = tile == centre_tile
                    rr, cc = (centre_row, centre_col) if cp else (-1, -1)
                    for br in range(r0 // bh, (r1 - 1) // bh + 1):
                        for bc in range(c0 // bw, (c1 - 1) // bw + 1):
                            requests[(tile, br, bc)].append(
                                (
                                    i,
                                    max(r0, br * bh) - br * bh,
                                    min(r1, (br + 1) * bh) - br * bh,
                                    max(c0, bc * bw) - bc * bw,
                                    min(c1, (bc + 1) * bw) - bc * bw,
                                    (
                                        rr - br * bh
                                        if cp
                                        and r0 <= rr < r1
                                        and c0 <= cc < c1
                                        and rr // bh == br
                                        and cc // bw == bc
                                        else -1
                                    ),
                                    (
                                        cc - bc * bw
                                        if cp
                                        and r0 <= rr < r1
                                        and c0 <= cc < c1
                                        and rr // bh == br
                                        and cc // bw == bc
                                        else -1
                                    ),
                                )
                            )
            # At an exact grid boundary, floating point may select a centre
            # pixel adjacent to the positive-area footprint window. Preserve
            # the original coordinate and consult that exact sample too.
            tile = centre_tile
            ds = centre_ds
            rr, cc = centre_row, centre_col
            already = any(
                w["tile"] == tile
                and w["window"][1] <= rr < w["window"][1] + w["window"][3]
                and w["window"][0] <= cc < w["window"][0] + w["window"][2]
                for w in windows[i]
            )
            if not already:
                bh, bw = ds.block_shapes[0]
                br, bc = rr // bh, cc // bw
                windows[i].append(
                    dict(tile=tile, window=[cc, rr, 1, 1], role="exact_VIIRS_centre")
                )
                requests[(tile, br, bc)].append(
                    (
                        i,
                        rr - br * bh,
                        rr - br * bh + 1,
                        cc - bc * bw,
                        cc - bc * bw + 1,
                        rr - br * bh,
                        cc - bc * bw,
                    )
                )
        for (tile, br, bc), query in sorted(requests.items()):
            a = self.block(tile, br, bc)
            for i, r0, r1, c0, c1, rr, cc in query:
                values, n = np.unique(a[r0:r1, c0:c1], return_counts=True)
                if any(int(v) not in {0, *LAND_CODES, *RISK_CODES} for v in values):
                    raise ValueError("Unexpected WorldCover class")
                hist[i].update({int(v): int(count) for v, count in zip(values, n)})
                if rr >= 0:
                    centre[i] = int(a[rr, cc])
        self.io["sampled_points"] += len(items)
        return [
            dict(
                available=bool(supported[i] and hist[i] and 0 not in hist[i]),
                centre=centre[i],
                support_classes={str(k): v for k, v in sorted(hist[i].items())},
                windows=windows[i],
                unavailable_reason=(
                    None
                    if supported[i] and hist[i] and 0 not in hist[i]
                    else (
                        "outside_product_support"
                        if not supported[i]
                        else "valid_NoData_or_partial_support"
                    )
                ),
            )
            for i in range(len(items))
        ]

    def close(self):
        for ds in self.handles.values():
            ds.close()
        self.handles.clear()
        self.blocks.clear()
        self.bytes = 0
