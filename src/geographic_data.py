"""Verified offline geographic evidence and feature-scoped review decisions.

The additional mask rejects only corroborated water interiors. An absence of
water evidence is not a dry-land or access assertion. Human decisions refer to
spatial context, not spot identifiers or complete source-archive fingerprints.
"""
from __future__ import annotations

from collections import Counter
import gzip
import hashlib
import json
import math
from pathlib import Path

from shapely import normalize, points, to_wkb
from shapely.geometry import Point, box, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / 'input/geography/manifest.json'
POLICY = 'reviewed-water-v1'
E3_POLICY = 'geographic-confidence-e3-v1'
REGIONS = frozenset({'france', 'uk_ireland', 'canaries'})
# WBM land is a production invariant. Neither water/NoData nor technical or
# collection failures can be waived by a cartographic uncertainty review.
REVIEWABLE_REASONS = frozenset({'marine_disagreement', 'inland_water_disagreement',
                              'tidalflat_uncertainty'})


def acceptance_part(reason, properties, clipped):
    """A category's local geometry, semantic class and stable source identity.

    Archive timestamps and shapefile record numbers are deliberately absent.
    A new overlapping feature/source must not inherit an old acceptance.
    """
    kind, cls = properties['kind'], properties.get('class', '')
    relevant = ((reason == 'marine_disagreement' and kind == 'marine_land')
                or (reason == 'inland_water_disagreement' and kind == 'water')
                or (reason == 'tidalflat_uncertainty' and kind == 'wetland'
                    and cls == 'wetland_tidalflat'))
    if relevant:
        return (kind, cls, properties.get('source', ''), properties.get('id', ''),
                geometry_digest(clipped))


def is_fingerprint(value):
    return (isinstance(value, str) and len(value) == 64
            and all(c in '0123456789abcdef' for c in value))


def validate_review_document(document):
    if document.get('schema') in {3,4} and document.get('policy')==E3_POLICY:
        for decision in document.get('decisions',[]):
            if decision.get('conclusion')!='resolve_risk':continue
            if document['schema']!=4:
                raise ValueError('B2 resolutions require explicit renewal under review schema 4')
            for resolution in decision.get('resolutions',[]):
                validate_positive_resolution(resolution)
        return
    if document.get('schema') not in {1, 2} or document.get('policy') != POLICY:
        raise ValueError('Incompatible geographic review schema or policy')
    if document['schema'] == 1 and any(d['conclusion'] == 'accept_uncertainty'
                                      for d in document['decisions']):
        raise ValueError('Scoped uncertainty acceptances require review schema 2')


def validate_positive_resolution(resolution):
    """Validate a human's explicit assertion, not the truth of its evidence."""
    positive=resolution.get('conclusion')
    if (not isinstance(positive,dict) or positive.get('outcome')!='concern_resolved'
            or positive.get('generally_above_water') is not True
            or not isinstance(positive.get('basis'),str) or not positive['basis'].strip()):
        raise ValueError('B2 resolution requires an explicit positive geographic conclusion')
    for key in ['reviewed_by','review_reference']:
        if not isinstance(resolution.get(key),str) or not resolution[key].strip():
            raise ValueError(f'B2 resolution requires identifiable {key}')
    # A named source without an identifiable evidence item or its relationship
    # to this risk is not a substantive, reason-specific review record.
    evidence=resolution.get('evidence')
    if not isinstance(evidence,list) or not evidence or any(
        not isinstance(e,dict) or any(not isinstance(e.get(k),str) or not e[k].strip()
                                     for k in ['source','reference','resolved_reason','finding'])
        or e['resolved_reason']!=resolution.get('reason') for e in evidence):
        raise ValueError('B2 resolution requires identifiable evidence tied to its specific reason')


def validate_licensing(value):
    if value.get('geometry_distribution') != 'ODbL-1.0':
        raise ValueError('An explicit ODbL geometry-distribution assessment is required')
    if value.get('spot_distribution') not in {'pending','assessed'}:
        raise ValueError('Invalid generated-spot licensing assessment status')
    if value['spot_distribution']=='assessed':
        record=value.get('spot_assessment_record',{})
        if not all(record.get(k) for k in ['reference','reviewed_by','resulting_database_license']):
            raise ValueError('Generated-spot licensing requires its own explicit assessment record')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def file_digest(path):
    checksum=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            checksum.update(block)
    return checksum.hexdigest()


def geometry_digest(geometry):
    return hashlib.sha256(to_wkb(normalize(geometry), byte_order=1)).hexdigest()


def valid_geometry(value, *, polygon=False):
    geometry = shape(value)
    # A crossing ring with a crossing hole can be invalid only in the planar
    # representation. Unwrap that representation before validating it; do not
    # repair unrelated invalid source geometries.
    polygons = list(geometry.geoms) if geometry.geom_type == 'MultiPolygon' else [geometry]
    if polygon and not geometry.is_valid and any(
        p.geom_type == 'Polygon' and any(abs(a[0]-b[0]) > 180
            for a,b in zip(p.exterior.coords,list(p.exterior.coords)[1:])) for p in polygons):
        from .geographic_geometry import canonical
        geometry = canonical(geometry)
    if geometry.is_empty or not geometry.is_valid:
        raise ValueError('Empty or invalid geographic geometry; explicit source review required')
    if polygon and geometry.geom_type not in {'Polygon', 'MultiPolygon'}:
        raise ValueError('Expected a polygon geographic geometry')
    if any(not math.isfinite(x) for x in geometry.bounds):
        raise ValueError('Non-finite geographic geometry')
    west, south, east, north = geometry.bounds
    if not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90):
        raise ValueError('Geographic geometry must use WGS84 longitude/latitude')
    return geometry


def records(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def spatial_context(features, extent):
    """Ignore geometry changes outside a review extent, retaining local holes.

    Feature numbering, archive dates and irrelevant descriptive attributes do
    not change the local geographic finding. Relevant classifications do.
    """
    parts = set()
    for feature in features:
        geometry = valid_geometry(feature['geometry'], polygon=True)
        if not geometry.intersects(extent):
            continue
        clipped = geometry.intersection(extent)
        if clipped.is_empty:
            continue
        properties = feature['properties']
        parts.add((properties['kind'], properties.get('class', ''), geometry_digest(clipped)))
    return digest(sorted(parts))


class GeographicEvidence:
    """Verified assets usable for preparation even while reviews need renewal.

    This object has no qualifier or publication audit interface.
    """
    def __init__(self, manifest=DEFAULT_MANIFEST, *, regions=None):
        self.path = Path(manifest).resolve()
        self.manifest = json.loads(self.path.read_text(encoding='utf-8'))
        if (self.manifest.get('schema'),self.manifest.get('policy')) not in {(1,POLICY),(2,E3_POLICY)}:
            raise ValueError('Incompatible geographic dataset schema or policy')
        self.revision = file_digest(self.path)
        validate_licensing(self.manifest.get('licensing',{}))
        self.entries = {}
        for name, entry in self.manifest.get('files', {}).items():
            path = (self.path.parent / entry['path']).resolve()
            if not path.is_relative_to(self.path.parent):
                raise ValueError('Geographic file path escapes dataset directory')
            self.entries[name] = path
        self.verify()
        self.regions = {name:dict(entry) for name,entry in self.manifest['regions'].items()}
        if not self.regions:
            raise ValueError('Empty geographic regional registry')
        if regions is None:
            from .regions import load_regions
            regions = load_regions()
        if set(self.regions) != set(regions):
            raise ValueError('Geographic dataset does not cover the configured regional registry')
        for name, region in self.regions.items():
            if len(region['bbox']) != 4:
                raise ValueError(f'Invalid geographic bbox for {name}')
            from .geographic_geometry import domain
            region['domain'] = domain(region['bbox'])
            self.require_region(name,regions[name])

    def verify(self):
        if file_digest(self.path) != self.revision:
            raise ValueError('Geographic manifest changed after verification')
        for name, path in self.entries.items():
            info = self.manifest['files'][name]
            if not path.is_file() or path.stat().st_size != info['size'] or file_digest(path) != info['sha256']:
                raise ValueError(f'Geographic dataset integrity failure: {name}')

    def require_region(self, name, region):
        if name not in self.regions:
            raise ValueError(f'Geographic dataset missing region: {name}')
        expected = self.regions[name]
        if expected['bbox'] != region['bbox'] or set(expected['countries']) != set(region['osm_country_code']):
            raise ValueError(f'Geographic dataset incompatible with region: {name}')


class GeographicDataset(GeographicEvidence):
    def __init__(self,manifest=DEFAULT_MANIFEST,*,regions=None,require_e3=False):
        super().__init__(manifest,regions=regions)
        self.is_e3=self.manifest.get('policy')==E3_POLICY
        if require_e3:self.require_e3()
        self.review_document = json.loads(self.entries['reviews'].read_text(encoding='utf-8'))
        validate_review_document(self.review_document)
        if self.is_e3 and (self.review_document.get('schema') not in {3,4} or self.review_document.get('policy')!=E3_POLICY):
            raise ValueError('E3 requires schema-3/4 reviewed decisions, not legacy acceptances')
        self.decisions_revision = file_digest(self.entries['reviews'])
        self.decisions = []
        seen = set()
        for raw in self.review_document['decisions']:
            identifier = raw['id']
            if not isinstance(identifier,str) or not identifier:
                raise ValueError('Geographic review identifiers must be nonempty strings')
            if identifier in seen:
                raise ValueError(f'Duplicate geographic review decision: {identifier}')
            seen.add(identifier)
            if raw['conclusion'] not in ({'corroborated_water','accept_uncertainty','hold','resolve_risk'} if self.is_e3 else {'corroborated_water', 'accept_uncertainty', 'hold'}):
                raise ValueError(f'Unexpected geographic decision: {identifier}')
            if not raw.get('rationale') or not raw.get('evidence') or not raw.get('provenance'):
                raise ValueError(f'Incomplete geographic decision evidence: {identifier}')
            acceptances = raw.get('resolutions' if raw['conclusion']=='resolve_risk' else 'acceptances', [])
            if raw['conclusion'] in {'accept_uncertainty','resolve_risk'}:
                if not isinstance(acceptances, list) or not acceptances:
                    raise ValueError(f'Unscoped geographic uncertainty acceptance: {identifier}')
                reasons = set()
                for acceptance in acceptances:
                    reason = acceptance.get('reason')
                    if self.is_e3:
                        from .geographic_e3 import B1_REASONS,B2_REASONS
                        allowed=B2_REASONS if raw['conclusion']=='resolve_risk' else B1_REASONS
                    else:allowed=REVIEWABLE_REASONS
                    if reason not in allowed or reason in reasons:
                        raise ValueError(f'Invalid or duplicate acceptance category: {identifier}')
                    if (not acceptance.get('evidence') or not acceptance.get('provenance')
                            or not is_fingerprint(acceptance.get('context_sha256'))):
                        raise ValueError(f'Incomplete scoped acceptance evidence: {identifier}')
                    reasons.add(reason)
            from .geographic_geometry import canonical
            extent = canonical(valid_geometry(raw['extent'], polygon=True))
            if raw.get('region') not in self.regions or not self.regions[raw['region']]['domain'].covers(extent):
                raise ValueError(f'Geographic review outside its prepared domain: {identifier}')
            context=raw.get('context_sha256')
            active = (is_fingerprint(context)
                      and context == self.manifest.get('contexts', {}).get(identifier)
                      and raw.get('extent_sha256') == geometry_digest(extent))
            if raw['conclusion'] == 'corroborated_water' and not active:
                raise ValueError(f'Stale corroborated-water decision: {identifier}')
            self.decisions.append(dict(raw, extent_geometry=extent, active=active))
        # Entire extents of corroborated-water decisions are evidence-backed
        # exclusions. No candidate IDs, pixel coordinates or distance buffers.
        self.water = [d for d in self.decisions if d['active'] and d['conclusion'] == 'corroborated_water']
        self.water_tree = STRtree([d['extent_geometry'] for d in self.water])
        self._e3_engine=None

    def require_e3(self):
        if self.manifest.get('schema')!=2 or self.manifest.get('policy')!=E3_POLICY:
            raise ValueError('Error: production requires a verified E3 geographic package; prepare an explicit upgrade')

    def classify(self,candidates,region,sampler):
        self.require_e3()
        if self._e3_engine is None:
            from .geographic_e3 import E3Evidence
            self._e3_engine=E3Evidence(self)
        return self._e3_engine.classify(candidates,region,sampler)

    def close(self):
        if self._e3_engine is not None:self._e3_engine.close();self._e3_engine=None

    def water_matches(self, candidates):
        """Return strict-interior matches, with real polygon boundaries allowed."""
        if not candidates or not self.water:
            return {}
        coordinates = points([(p['lon'], p['lat']) for p in candidates])
        matches = self.water_tree.query(coordinates, predicate='within')
        result = {}
        for candidate, decision in matches.T:
            result.setdefault(int(candidate), []).append(self.water[int(decision)]['id'])
        from .geographic_geometry import contains
        for i,p in enumerate(candidates):
            if abs(p['lon'])==180:
                for d in self.water:
                    if contains(d['extent_geometry'],Point(p['lon'],p['lat'])):
                        result.setdefault(i,[]).append(d['id'])
        return {index:sorted(set(ids)) for index,ids in sorted(result.items())}

    def qualify(self, candidates, *, rejected=None):
        matches = self.water_matches(candidates)
        if rejected is not None:
            rejected.extend(dict(point=dict(candidates[i]), decisions=ids,
                                 reason='corroborated_water_interior') for i, ids in matches.items())
        return [p for i, p in enumerate(candidates) if i not in matches]

    def audit(self, spots):
        """Audit final positions only; stream complete regional evidence layers."""
        if self.is_e3:
            from .geographic_e3 import audit
            return audit(self,spots)
        coordinates = []
        owners = []
        for spot in spots:
            lat, lon = float(spot['lat']), float(spot['lon'])
            if not math.isfinite(lat) or not math.isfinite(lon):
                raise ValueError('Non-finite publication coordinates')
            point = Point(lon, lat)
            matches = [name for name, region in self.regions.items()
                       if spot.get('country') in region['countries'] and region['domain'].covers(point)]
            if len(matches) != 1:
                raise ValueError(f'Publication spot has no unique configured geographic domain: {spot.get("id")}')
            coordinates.append(point)
            owners.append(matches[0])
        tree = STRtree(coordinates)
        findings = [dict(spot=dict(spot), marine_land=False, inland_coverage=False,
                         water_features=[], wetland_features=[]) for spot in spots]
        for region_name in sorted(set(owners)):
            entry = self.regions[region_name]
            self._audit_wbm(region_name, spots, owners, findings)
            for feature in records(self.entries[entry['evidence']]):
                # Bboxes are derived during verified preparation, never used
                # as exclusion shapes. Avoid constructing detailed polygons
                # whose envelopes contain no final publication positions.
                if 'bbox' in feature and not len(tree.query(box(*feature['bbox']))):
                    continue
                geometry = valid_geometry(feature['geometry'], polygon=True)
                properties = feature['properties']
                kind = properties['kind']
                if kind not in {'marine_land', 'inland_coverage', 'water', 'wetland'}:
                    raise ValueError(f'Unexpected geographic evidence kind: {kind}')
                for index in tree.query(geometry, predicate='covers'):
                    index = int(index)
                    if owners[index] != region_name:
                        continue
                    finding = findings[index]
                    if kind == 'marine_land':
                        finding['marine_land'] = True
                    elif kind == 'inland_coverage':
                        finding['inland_coverage'] = True
                    elif kind in {'water', 'wetland'}:
                        finding['water_features' if kind == 'water' else 'wetland_features'].append(properties)
                    else:
                        raise ValueError(f'Unexpected geographic evidence kind: {kind}')
        water = self.water_matches(spots)
        counts = Counter()
        for i, finding in enumerate(findings):
            point = coordinates[i]
            applicable = [d for d in self.decisions if d['extent_geometry'].contains(point)]
            active = [d for d in applicable if d['active']]
            stale = [d['id'] for d in applicable if not d['active']]
            reasons = []
            if not finding['marine_land']:
                reasons.append('marine_disagreement')
            if finding['water_features']:
                reasons.append('inland_water_disagreement')
            if finding['wetland_features']:
                finding['observations'] = ['Mapped wetland does not by itself establish open water']
                if any(f.get('class')=='wetland_tidalflat' for f in finding['wetland_features']):
                    reasons.append('tidalflat_uncertainty')
            if finding['wbm_class'] != 0:
                reasons.append('wbm_nonland_disagreement')
            # Changed evidence cannot silently withdraw an explicit hold.
            # It remains binding until a new reviewed decision revision.
            held = any(d['conclusion'] == 'hold' for d in applicable)
            if held:
                reasons.append('reviewed_geographic_hold')
            missing = finding['marine_land'] and not finding['inland_coverage']
            if missing:
                reasons.append('incomplete_source_extract_coverage')
            reviewed_reasons = {a['reason'] for d in active if d['conclusion'] == 'accept_uncertainty'
                                for a in d['acceptances']
                                if a['context_sha256'] == self.manifest.get('acceptance_contexts', {})
                                .get(d['id'], {}).get(a['reason'])}
            if i in water:
                status = 'reject_water'
            elif missing:
                status = 'hold'
            elif reasons:
                # A hold or stale local review cannot be overridden by a
                # broader acceptance. Water decisions never act as waivers.
                accepted = set(reasons) <= reviewed_reasons
                status = 'accepted_uncertainty' if accepted and not held and not stale else 'hold'
            else:
                status = 'no_additional_objection'
            finding.update(status=status, reasons=reasons,
                           accepted_reasons=sorted(set(reasons) & reviewed_reasons),
                           unreviewed_reasons=sorted(set(reasons) - reviewed_reasons),
                           decisions=[d['id'] for d in active], stale_decisions=stale)
            counts[status] += 1
        return dict(status='passed' if not (counts['reject_water'] or counts['hold']) else 'blocked',
                    counts=dict(counts), final_spot_count=len(spots), findings=findings,
                    geographic_revision=self.revision, decisions_revision=self.decisions_revision,
                    policy=POLICY,
                    limitations='No ground-truth, accessibility or OSM mapping-completeness guarantee. No additional objection is not proof of dry ground.')

    def _audit_wbm(self, region_name, spots, owners, findings):
        """Require one explicit verified WBM sample for every final position."""
        from .elevation import ElevationSampler, WATER_CLASSES, WBM_NODATA
        terrain = self.manifest.get('terrain', {}).get(region_name)
        if not terrain or terrain not in self.entries:
            raise ValueError(f'Missing verified WBM terrain reference: {region_name}')
        manifest = json.loads(self.entries[terrain].read_text(encoding='utf-8'))
        if manifest.get('region') != region_name:
            raise ValueError(f'WBM terrain region mismatch: {region_name}')
        indexes = [i for i, owner in enumerate(owners) if owner == region_name]
        with ElevationSampler.water_masks(self.entries[terrain], self.path.parent) as sampler:
            values = sampler.wbm_classes([spots[i]['lat'] for i in indexes],
                                         [spots[i]['lon'] for i in indexes])
        if values is None or len(values) != len(indexes):
            raise ValueError(f'Missing WBM audit samples: {region_name}')
        for index, value in zip(indexes, values):
            if value not in {0, *WATER_CLASSES, WBM_NODATA}:
                raise ValueError(f'Invalid WBM audit sample: {region_name}: {value!r}')
            findings[index]['wbm_class'] = int(value)
