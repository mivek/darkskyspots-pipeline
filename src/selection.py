"""Batch geographic qualification shared by generation and comparisons."""
from collections import Counter
import numpy as np
from shapely import box, intersects, prepare
from shapely.ops import unary_union

from .elevation import WATER_CLASSES, WBM_NODATA
from .geography import classify_candidates, load_geography


class CandidateQualifier:
    """Reuse country attribution before consulting the mandatory local WBM.

    ``water=False`` is reserved for the historical comparison scenarios A/C.
    Production explicitly supplies a sampler and always uses ``water=True``.
    """

    def __init__(self, region, transform, crs, sampler, *, water=True,
                 geography=None, classify=classify_candidates):
        if water and sampler is None:
            raise ValueError('WBM qualification requires a verified terrain sampler')
        self.region, self.transform, self.crs = region, transform, crs
        self.sampler, self.water = sampler, water
        self.geography, self.classify = geography or load_geography(), classify
        self.counts = Counter({name: 0 for name in
                               ['wbm_land', 'wbm_nodata', 'wbm_ocean', 'wbm_lake', 'wbm_river',
                                'geographically_empty_cells']})
        codes = region['osm_country_code']
        if isinstance(codes, str):
            codes = [codes]
        self.extent = unary_union([self.geography.countries[c] for c in codes]).intersection(box(*region['bbox']))
        prepare(self.extent)
        prepare(self.geography.land)

    def possible_cells(self, bounds):
        """Conservative envelope rejection, never a country assignment.

        A cell disjoint from either land or allowed-country/bbox geometry
        cannot contain an admissible centre. All remaining centres still go
        through classify_candidates, including its source-pixel area rule.
        """
        rectangles = box(*np.asarray(bounds).T)
        possible = intersects(self.extent, rectangles) & intersects(self.geography.land, rectangles)
        self.counts['geographically_empty_cells'] += int(np.count_nonzero(~possible))
        return possible

    def __call__(self, candidates):
        codes = self.region['osm_country_code']
        if isinstance(codes, str):
            codes = [codes]
        kept, stats = self.classify(candidates, codes, geography=self.geography,
                                    transform=self.transform, crs=self.crs,
                                    equal_area_epsg=self.region['equal_area_epsg'])
        self.counts.update(stats)
        west, south, east, north = self.region['bbox']
        inside = [p for p in kept if west <= p['lon'] <= east and south <= p['lat'] <= north]
        self.counts['outside_bbox'] += len(kept) - len(inside)
        if not self.water or not inside:
            return inside
        classes = self.sampler.wbm_classes([p['lat'] for p in inside], [p['lon'] for p in inside])
        if len(classes) != len(inside):
            raise ValueError('Invalid WBM qualification result length')
        result = []
        for p, value in zip(inside, classes):
            if value == 0:
                self.counts['wbm_land'] += 1
                result.append(p)
            elif value == WBM_NODATA:
                self.counts['wbm_nodata'] += 1
            elif value in WATER_CLASSES:
                self.counts['wbm_' + WATER_CLASSES[value]] += 1
            else:
                raise ValueError(f'Invalid WBM qualification class: {value}')
        return result
