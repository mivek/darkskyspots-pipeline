"""Batch geographic qualification shared by generation and comparisons."""
from collections import Counter
import numpy as np
from shapely import box, intersects, prepare
from shapely.ops import unary_union

from .elevation import WATER_CLASSES, WBM_NODATA
from .geography import classify_candidates, load_geography
from .geographic_geometry import domain, inside


class CandidateQualifier:
    """Reuse country attribution before consulting the mandatory local WBM.

    ``water=False`` is reserved for the historical comparison scenarios A/C.
    Production explicitly supplies a sampler and always uses ``water=True``.
    """

    def __init__(self, region, transform, crs, sampler, *, water=True,
                 geography=None, classify=classify_candidates, reviewed_water=None,
                 rejection_sink=None,region_name=None):
        if water and sampler is None:
            raise ValueError('WBM qualification requires a verified terrain sampler')
        self.region, self.transform, self.crs = region, transform, crs
        self.sampler, self.water = sampler, water
        self.reviewed_water = reviewed_water
        self.rejection_sink = rejection_sink
        self.region_name=region_name
        self.reviewed_rejections = []
        self.geography, self.classify = geography or load_geography(), classify
        self.counts = Counter({name: 0 for name in
                               ['wbm_land', 'wbm_nodata', 'wbm_ocean', 'wbm_lake', 'wbm_river',
                                'geographically_empty_cells']})
        codes = region['osm_country_code']
        if isinstance(codes, str):
            codes = [codes]
        self.extent = unary_union([self.geography.countries[c] for c in codes]).intersection(domain(region['bbox']))
        prepare(self.extent)
        prepare(self.geography.land)

    def possible_cells(self, bounds):
        """Conservative envelope rejection, never a country assignment.

        A cell disjoint from either land or allowed-country/bbox geometry
        cannot contain an admissible centre. All remaining centres still go
        through classify_candidates, including its source-pixel area rule.
        """
        rectangles = box(*np.asarray(bounds).T)
        if np.any(np.asarray(bounds)[:,0]<-180) or np.any(np.asarray(bounds)[:,2]>180):
            from .geographic_geometry import canonical
            rectangles=[canonical(g) for g in rectangles]
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
        within = [p for p in kept if inside(p['lon'],p['lat'],self.region['bbox'])]
        self.counts['outside_bbox'] += len(kept) - len(within)
        if not self.water or not within:
            return within
        if self.reviewed_water is not None and getattr(self.reviewed_water,'is_e3',False) is True:
            # The shared classifier reads WBM once and supplies native support
            # for both the indexed and complementary raster checks.
            name=self.region_name or next((n for n,r in self.reviewed_water.regions.items()
                       if r['bbox']==self.region['bbox'] and set(r['countries'])==set(codes)),None)
            if name is None:raise ValueError('No unique E3 region for qualification')
            findings=self.reviewed_water.classify(within,name,self.sampler)
            if len(findings)!=len(within):raise ValueError('Missing E3 geographic qualification result')
            result=[]
            for p,f in zip(within,findings):
                self.counts['confidence_'+f['confidence']]+=1
                value=f['base_evidence']['wbm_class']
                label='nodata' if value==WBM_NODATA else 'land' if value==0 else WATER_CLASSES[value]
                self.counts['wbm_'+label]+=1
                if 'reviewed_high_confidence_water' in f['reasons']:
                    self.counts['reviewed_water_rejected']+=1
                if f['confidence']!='C':
                    p['_geographic_level']=f['confidence'];result.append(p)
                elif self.rejection_sink is not None:self.rejection_sink(f)
            return result
        classes = self.sampler.wbm_classes([p['lat'] for p in within], [p['lon'] for p in within])
        if len(classes) != len(within):
            raise ValueError('Invalid WBM qualification result length')
        result = []
        for p, value in zip(within, classes):
            if value == 0:
                self.counts['wbm_land'] += 1
                result.append(p)
            elif value == WBM_NODATA:
                self.counts['wbm_nodata'] += 1
            elif value in WATER_CLASSES:
                self.counts['wbm_' + WATER_CLASSES[value]] += 1
            else:
                raise ValueError(f'Invalid WBM qualification class: {value}')
        if self.reviewed_water is not None:
            rejected = []
            accepted = self.reviewed_water.qualify(result, rejected=rejected)
            if self.rejection_sink is None:
                self.reviewed_rejections.extend(rejected)
            else:
                for record in rejected:
                    self.rejection_sink(record)
            self.counts['reviewed_water_rejected'] += len(result) - len(accepted)
            return accepted
        return result
