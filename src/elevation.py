"""Local Copernicus DEM/WBM sampling and integrity checks."""
import hashlib
import json
import math
from collections import Counter, OrderedDict
from contextlib import ExitStack
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform

WATER_CLASSES = {1: 'ocean', 2: 'lake', 3: 'river'}
WBM_NODATA = -1


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


class ElevationSampler:
    """Sample paired, manifest-verified elevation and water rasters locally."""

    def __init__(self, manifest, cache_dir, *, wbm_cache_bytes=64 * 1024 * 1024):
        self.manifest_path = Path(manifest)
        self.cache_dir = Path(cache_dir).resolve()
        self.stack = ExitStack()
        self.tiles = {}
        self.counts = Counter({name: 0 for name in WATER_CLASSES.values()})
        self.wbm_cache_limit = int(wbm_cache_bytes)
        if self.wbm_cache_limit < 0:
            raise ValueError('WBM cache size must be nonnegative')
        self.wbm_cache = OrderedDict()
        self.wbm_cache_size = 0
        self.wbm_io = Counter()
        try:
            self.stack.enter_context(rasterio.Env(GDAL_CACHEMAX=32 * 1024 * 1024))
            data = json.loads(self.manifest_path.read_text(encoding='utf-8'))
            if data.get('schema') != 1 or not data.get('tiles'):
                raise ValueError(f'Invalid elevation manifest: {manifest}')
            for tile in data['tiles']:
                key = (int(tile['lat']), int(tile['lon']))
                if key in self.tiles or tile['product'] not in ('GLO-30', 'GLO-90'):
                    raise ValueError(f'Invalid or duplicate DEM tile: {key}')
                pair = []
                for layer in ('dem', 'wbm'):
                    info = tile[layer]
                    path = (self.cache_dir / info['path']).resolve()
                    if not path.is_relative_to(self.cache_dir) or not path.is_file():
                        raise ValueError(f'Missing elevation {layer}: {path}')
                    if path.stat().st_size != info['size'] or sha256(path) != info['sha256']:
                        raise ValueError(f'Corrupt elevation {layer}: {path}')
                    ds = self.stack.enter_context(rasterio.open(path))
                    if ds.count != 1 or ds.crs is None:
                        raise ValueError(f'Invalid elevation raster: {path}')
                    pair.append(ds)
                dem, wbm = pair
                if (dem.crs != wbm.crs or dem.transform != wbm.transform
                        or dem.width != wbm.width or dem.height != wbm.height):
                    raise ValueError(f'Mismatched DEM and WBM grids: {key}')
                self.tiles[key] = tuple(pair)
        except BaseException:
            self.stack.close()
            raise

    def close(self):
        self.wbm_cache.clear()
        self.wbm_cache_size = 0
        self.stack.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def wbm_classes(self, lats, lons):
        """Read WGS84 points by native raster block, with a bounded LRU.

        Returns 0/1/2/3, or WBM_NODATA for valid masked pixels. Technical
        failures and unknown classes raise; altitude counters are untouched.
        Cache accounting includes both the values and their validity masks.
        """
        lats, lons = np.asarray(lats, dtype=float), np.asarray(lons, dtype=float)
        if (lats.ndim != 1 or lats.shape != lons.shape
                or not np.all(np.isfinite(lats)) or not np.all(np.isfinite(lons))
                or np.any((lats < -90) | (lats >= 90) | (lons < -180) | (lons >= 180))):
            raise ValueError('Invalid WGS84 coordinates for WBM')
        result = np.full(lats.size, WBM_NODATA, dtype=np.int16)
        groups = {}
        for i, key in enumerate(zip(np.floor(lats).astype(int), np.floor(lons).astype(int))):
            groups.setdefault(key, []).append(i)
        for key, positions in groups.items():
            if key not in self.tiles:
                raise ValueError(f'No prepared DEM tile for WBM: {key}')
            ds = self.tiles[key][1]
            xs, ys = lons[positions], lats[positions]
            if ds.crs != rasterio.crs.CRS.from_epsg(4326):
                xs, ys = transform('EPSG:4326', ds.crs, xs, ys)
                self.wbm_io['transformed_points'] += len(positions)
            rows, cols = rasterio.transform.rowcol(ds.transform, xs, ys)
            rows, cols = np.asarray(rows), np.asarray(cols)
            if np.any((rows < 0) | (cols < 0) | (rows >= ds.height) | (cols >= ds.width)):
                raise ValueError(f'Coordinate outside WBM raster {ds.name}')
            bh, bw = ds.block_shapes[0]
            blocks = {}
            for j, (row, col) in enumerate(zip(rows, cols)):
                blocks.setdefault((int(row) // bh, int(col) // bw), []).append(j)
            for (br, bc), indexes in blocks.items():
                cache_key = (key, br, bc)
                if cache_key in self.wbm_cache:
                    data, invalid = self.wbm_cache.pop(cache_key)
                    self.wbm_cache[cache_key] = (data, invalid)
                    self.wbm_io['cache_hits'] += 1
                else:
                    window = rasterio.windows.Window(bc * bw, br * bh,
                                                     min(bw, ds.width - bc * bw),
                                                     min(bh, ds.height - br * bh))
                    array = ds.read(1, window=window, masked=True)
                    data = np.array(array.data, copy=True)
                    # Only the dataset's valid NoData mask is a recoverable
                    # missing pixel. Unmasked NaN/Inf are invalid classes.
                    invalid = np.ma.getmaskarray(array).copy()
                    self.wbm_io['block_reads'] += 1
                    cost = data.nbytes + invalid.nbytes
                    if cost <= self.wbm_cache_limit:
                        while self.wbm_cache and self.wbm_cache_size + cost > self.wbm_cache_limit:
                            old_data, old_mask = self.wbm_cache.popitem(last=False)[1]
                            self.wbm_cache_size -= old_data.nbytes + old_mask.nbytes
                        self.wbm_cache[cache_key] = (data, invalid)
                        self.wbm_cache_size += cost
                        self.wbm_io['peak_cache_bytes'] = max(self.wbm_io['peak_cache_bytes'], self.wbm_cache_size)
                rr, cc = rows[indexes] - br * bh, cols[indexes] - bc * bw
                values, missing = data[rr, cc], invalid[rr, cc]
                if np.any(~missing & ~np.isin(values, (0, 1, 2, 3))):
                    bad = np.unique(values[~missing & ~np.isin(values, (0, 1, 2, 3))])
                    raise ValueError(f'Invalid WBM value in {ds.name}: {bad.tolist()}')
                dest = np.asarray(positions)[indexes]
                result[dest[~missing]] = values[~missing].astype(np.int16)
        self.wbm_io['points'] += lats.size
        return result

    @staticmethod
    def _read(ds, lon, lat):
        x, y = transform('EPSG:4326', ds.crs, [lon], [lat])
        row, col = ds.index(x[0], y[0])
        if row < 0 or col < 0 or row >= ds.height or col >= ds.width:
            raise ValueError(f'Coordinate outside raster {ds.name}: {lat}, {lon}')
        value = ds.read(1, window=((row, row + 1), (col, col + 1)), masked=True)[0, 0]
        return None if np.ma.is_masked(value) or not np.isfinite(value) else float(value)

    def sample(self, lat, lon):
        lat, lon = float(lat), float(lon)
        if not math.isfinite(lat) or not math.isfinite(lon) or not (-90 <= lat < 90 and -180 <= lon < 180):
            raise ValueError(f'Invalid coordinate: {lat}, {lon}')
        key = (math.floor(lat), math.floor(lon))
        if key not in self.tiles:
            raise ValueError(f'No prepared DEM tile for {lat}, {lon}: {key}')
        dem, wbm = self.tiles[key]
        mask = self._read(wbm, lon, lat)
        if mask is None:
            self.counts['nodata'] += 1
            return None
        if mask not in (0, 1, 2, 3):
            raise ValueError(f'Invalid WBM value at {lat}, {lon}: {mask}')
        if mask != 0:
            self.counts[WATER_CLASSES[mask]] += 1
            return None
        elevation = self._read(dem, lon, lat)
        if elevation is None:
            self.counts['nodata'] += 1
            return None
        self.counts['land'] += 1
        return int(round(elevation))
