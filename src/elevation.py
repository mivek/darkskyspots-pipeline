"""Local Copernicus DEM/WBM sampling and integrity checks."""
import hashlib
import json
import math
from collections import Counter
from contextlib import ExitStack
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform

WATER_CLASSES = {1: 'ocean', 2: 'lake', 3: 'river'}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


class ElevationSampler:
    """Sample paired, manifest-verified elevation and water rasters locally."""

    def __init__(self, manifest, cache_dir):
        self.manifest_path = Path(manifest)
        self.cache_dir = Path(cache_dir).resolve()
        self.stack = ExitStack()
        self.tiles = {}
        self.counts = Counter({name: 0 for name in WATER_CLASSES.values()})
        try:
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
        self.stack.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

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
