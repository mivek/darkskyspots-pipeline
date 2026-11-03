#!/usr/bin/env python3
"""Explicit preparation-time download of regional Geofabrik water layers.

Reuses or explicitly acquires a frozen index and OSMCoastline cache. Downloads ZIP
members needed for hydrographic evidence; never used by spot generation.
"""
from collections import OrderedDict
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import re
import shutil
import sys
import zipfile

import requests
from shapely.geometry import box, shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.geographic_data import file_digest
from src.regions import load_regions
from src.geography import load_geography
from tools.prepare_geography import source_members
from src.geographic_prepare import relevant_extracts


def bootstrap_cache(sources):
    """Acquire existing processed sources once, not an OSM planet pipeline."""
    sources.mkdir(parents=True,exist_ok=True)
    index=sources/'geofabrik-index.json'
    if not index.exists():
        response=requests.get('https://download.geofabrik.de/index-v1.json',timeout=90)
        response.raise_for_status()
        value=response.json()
        if value.get('type')!='FeatureCollection':raise ValueError('Invalid Geofabrik index')
        index.write_text(json.dumps(value,sort_keys=True)+'\n')
        (sources/'geofabrik-index-source.json').write_text(json.dumps(dict(
            url='https://download.geofabrik.de/index-v1.json',
            retrieved_utc=datetime.now(timezone.utc).isoformat(),sha256=file_digest(index)),indent=2)+'\n')
    archive=sources/'land-polygons-split-4326.zip'
    metadata=sources/'osm-land-source.json'
    if archive.exists()!=metadata.exists():
        raise ValueError('Incomplete marine source cache; use a new cache directory')
    if not archive.exists():
        url='https://osmdata.openstreetmap.de/download/land-polygons-split-4326.zip'
        print('Downloading processed OSMCoastline archive once',flush=True)
        response=requests.get(url,headers={'Accept-Encoding':'identity'},timeout=90,stream=True)
        response.raise_for_status()
        pending=archive.with_suffix('.part')
        with pending.open('wb') as stream:
            for block in response.iter_content(1024*1024):stream.write(block)
        if 'Content-Length' in response.headers and pending.stat().st_size!=int(response.headers['Content-Length']):
            raise ValueError('Truncated marine source archive')
        with zipfile.ZipFile(pending) as checked:
            if checked.testzip() is not None:raise ValueError('Corrupt marine source ZIP member')
        info=dict(provider='OSMCoastline land polygons / OpenStreetMap',url=url,
                  last_modified=response.headers.get('Last-Modified'),
                  retrieved_utc=datetime.now(timezone.utc).isoformat(),
                  size=pending.stat().st_size,sha256=file_digest(pending),license='ODbL 1.0')
        pending.replace(archive)
        metadata.write_text(json.dumps(info,indent=2)+'\n')
    land=sources/'osm-land'
    if not land.exists():
        info=json.loads(metadata.read_text())
        if archive.stat().st_size!=info['size'] or file_digest(archive)!=info['sha256']:
            raise ValueError('Corrupt cached marine source archive')
        land.mkdir()
        with zipfile.ZipFile(archive) as zipped:
            for member in zipped.infolist():
                name=Path(member.filename).name
                if Path(name).suffix.lower() not in {'.shp','.shx','.dbf','.prj','.cpg'}:continue
                with zipped.open(member) as source,(land/name).open('wb') as target:
                    shutil.copyfileobj(source,target)


class RemoteZip(io.RawIOBase):
    """Bounded HTTP-range reader pinned against mid-download replacement."""
    def __init__(self, url, session=None):
        super().__init__()
        self.session = session or requests.Session()
        response = self.session.head(url, headers={'Accept-Encoding':'identity'},allow_redirects=True, timeout=30)
        response.raise_for_status()
        self.url, self.size = response.url, int(response.headers['Content-Length'])
        self.etag, self.modified = response.headers.get('ETag'), response.headers.get('Last-Modified')
        self.pos, self.transferred, self.block_size = 0, 0, 4 * 1024 * 1024
        self.blocks = OrderedDict()

    def seekable(self):return True
    def readable(self):return True
    def tell(self):return self.pos
    def seek(self, offset, whence=0):
        self.pos = offset if whence == 0 else self.pos + offset if whence == 1 else self.size + offset
        if self.pos < 0:raise ValueError('Negative ZIP offset')
        return self.pos

    def read(self, n=-1):
        end = min(self.size, self.pos + n if n >= 0 else self.size)
        parts = []
        while self.pos < end:
            key = self.pos // self.block_size
            if key not in self.blocks:
                start = key * self.block_size
                stop = min(start + self.block_size, self.size) - 1
                headers = {'Range': f'bytes={start}-{stop}','Accept-Encoding':'identity'}
                validator=self.etag if self.etag and not self.etag.startswith('W/') else self.modified
                if not validator:raise ValueError('Range source has no usable snapshot validator')
                headers['If-Range']=validator
                response = self.session.get(self.url, headers=headers, timeout=90,stream=True)
                try:
                    # A changed If-Range representation returns 200. Reject
                    # it before downloading a complete replacement archive.
                    if response.status_code != 206:
                        raise ValueError(f'Pinned ZIP range request failed: {response.status_code}')
                    if response.headers.get('Content-Range') != f'bytes {start}-{stop}/{self.size}':
                        raise ValueError('Unexpected ZIP Content-Range')
                    if self.etag and response.headers.get('ETag',self.etag)!=self.etag:
                        raise ValueError('ZIP snapshot ETag changed during range download')
                    data=response.content
                    if len(data) != stop - start + 1:
                        raise ValueError('Short ZIP range response')
                finally:
                    response.close()
                self.blocks[key] = data
                self.transferred += len(data)
                while len(self.blocks) > 16:self.blocks.popitem(last=False)
            self.blocks.move_to_end(key)
            chunk = self.blocks[key]
            offset = self.pos - key * self.block_size
            take = min(end - self.pos, len(chunk) - offset)
            parts.append(chunk[offset:offset + take]);self.pos += take
        return b''.join(parts)


def fetch(sources, snapshot_date):
    sources = Path(sources)
    bootstrap_cache(sources)
    index = json.loads((sources / 'geofabrik-index.json').read_text())
    registry = load_regions()
    if set(registry)!={'france','uk_ireland','canaries'}:
        raise ValueError('Source preparation is limited to the three initial regions')
    inventory_path=sources/'source-selection.json'
    inventory=json.loads(inventory_path.read_text()) if inventory_path.exists() else dict(
        schema=1,index_sha256=file_digest(sources/'geofabrik-index.json'),snapshot_date=snapshot_date,unavailable={})
    if inventory['index_sha256']!=file_digest(sources/'geofabrik-index.json') or inventory['snapshot_date']!=snapshot_date:
        raise ValueError('Source inventory/index/date mismatch; use a new source cache')
    unavailable=inventory['unavailable']
    selected={name:url.replace('-latest-',f'-{snapshot_date}-')
              for name,url in relevant_extracts(index,registry,load_geography(),unavailable=unavailable).items()}
    if not selected:raise ValueError('No supported regional source extracts found')
    # A national layer already contains its child extracts. Do not download
    # England and every English county, or fingerprint duplicate coverage.
    for identifier, url in sorted(selected.items()):
        directory = sources / 'geofabrik' / identifier
        metadata = directory / 'source.json'
        if metadata.exists():
            source_members(metadata)
            print('Verified existing', identifier, flush=True)
            continue
        directory.mkdir(parents=True, exist_ok=True)
        try:
            remote = RemoteZip(url)
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code!=404:
                raise
            prefix=url.rsplit('/',1)[0]+'/'+url.rsplit('/',1)[1].split(f'-{snapshot_date}-')[0]+'/'
            children=[f for f in index['features'] if (f['properties'].get('urls',{}).get('shp') or '').startswith(prefix)]
            if not children:
                raise ValueError(f'No pinned shapefile or child packages available for required extent: {identifier}') from exc
            unavailable[identifier]=dict(url=url,http_status=404,
                reason='Pinned parent shapefile returned 404; select intersecting child extracts')
            inventory_path.write_text(json.dumps(inventory,sort_keys=True,indent=2)+'\n')
            print('Parent package unavailable; resolving child extracts',identifier,flush=True)
            return fetch(sources,snapshot_date)
        print('Downloading regional water', identifier, remote.size, 'archive bytes', flush=True)
        members = []
        with zipfile.ZipFile(remote) as archive:
            for info in archive.infolist():
                name = Path(info.filename).name
                if not re.fullmatch(r'gis_osm_water_a_free_\d+\.(shp|shx|dbf|prj|cpg)', name):continue
                target = directory / name
                pending = target.with_suffix(target.suffix + '.part')
                with archive.open(info) as incoming, pending.open('wb') as output:
                    shutil.copyfileobj(incoming, output)
                pending.replace(target)
                members.append(dict(path=name, member=info.filename, size=target.stat().st_size,
                                    sha256=file_digest(target)))
        if not any(f['path'].endswith('.shp') for f in members):
            raise ValueError(f'No hydrographic polygons in {url}')
        metadata.write_text(json.dumps(dict(provider='Geofabrik / OpenStreetMap',url=remote.url,
            last_modified=remote.modified,etag=remote.etag,retrieved_utc=datetime.now(timezone.utc).isoformat(),
            archive_size=remote.size,bytes_transferred=remote.transferred,license='ODbL 1.0',files=members),indent=2)+'\n')
        print('Prepared', identifier, remote.transferred, 'downloaded bytes', flush=True)
    inventory_path.write_text(json.dumps(inventory,sort_keys=True,indent=2)+'\n')


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources',required=True,type=Path)
    parser.add_argument('--snapshot-date',required=True,help='Pinned Geofabrik date, YYMMDD')
    args=parser.parse_args()
    try:
        if not re.fullmatch(r'\d{6}',args.snapshot_date):raise ValueError('Expected snapshot date YYMMDD')
        fetch(args.sources,args.snapshot_date)
    except Exception as exc:
        print(f'Error: {exc}',file=sys.stderr)
        raise SystemExit(1)
