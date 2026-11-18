# G2.1a DEM grid contract

This note records the geographic raster contract used to qualify the eight G2
halo cells. It applies to those appended cells only. Tenerife schema 1, Canary
G1 schema 2, and the pinned historical 56-pair UK/Ireland inventory keep their
existing compatibility and identity rules.

## Native GLO-30 DGED grid

The retained files under `input/elevation` are native Copernicus GLO-30 DGED
GeoTIFFs, not the separately derived COG layout. Inspection of retained N49
W007 files found 3601 columns × 3601 rows, EPSG:4326, `AREA_OR_POINT=Point`,
and 1 arc-second spacing in both directions. Retained N54 files found 2401
columns × 3601 rows, EPSG:4326, `AREA_OR_POINT=Point`, 1 arc-second latitude
spacing, and 1.5 arc-second longitude spacing. Their affine origins are half a
pixel outside the first north-west pixel center.

The Copernicus Product Handbook specifies GLO-30 DGED latitude spacing of 1.0
arc-second and longitude reduction factors of 1× below 50°, 1.5× from 50° to
60°, 2× from 60° to 70°, 3× from 70° to 80°, 5× from 80° to 85°, and 10× from
85° to 90°. The original DGED one-degree grids include both boundary posts.
Derived public COG layouts drop shared boundary rows/columns and use different
dimensions, so they do not satisfy this contract. See the [Copernicus DEM
Product Handbook](https://dataspace.copernicus.eu/sites/default/files/media/files/2024-06/geo1988-copernicusdem-spe-002_producthandbook_i5.0.pdf)
and the [CDSE native product type table](https://documentation.dataspace.copernicus.eu/Data/Others/CCM.html).

For a cell `[lat, lat+1] × [lon, lon+1]`, the expected pixel-center lattice is
one-degree boundary posts. If `dx` and `dy` are the expected longitude and
latitude spacings, the GeoTIFF affine is:

```text
(dx, 0, lon - dx/2,
  0, -dy, lat + 1 + dy/2)
```

This makes the raster's outer bounds extend by half a pixel around the actual
post lattice. For N55 W009, DGED has 2401 columns × 3601 rows, `dx=1.5/3600°`,
`dy=1/3600°`, pixel-center origin `(-9°, 56°)`, and affine origin
`(-9.000208333333333°, 56.00013888888889°)`. Its expected bounds are
`[-9.000208333333333°, 54.99986111111111°, -7.999791666666667°,
56.00013888888889°]`.

The validator requires native EPSG:4326 GeoTIFF, RasterPixelIsPoint metadata,
one band with the product's float32 DEM or uint8 WBM type, latitude-specific
dimensions and spacing, north-up orientation, the exact expected affine, and
no GCP/RPC override. DEM and WBM must each match the declared cell and each
other. Affine coefficients use an absolute tolerance of `1e-10` degrees to
cover floating-point serialization; this is far below one pixel and does not
permit a half-pixel or full-pixel displacement.

## Integrity and provenance boundaries

The checks establish separate facts:

- **File integrity:** local size and SHA-256 match the DEM manifest. The
  sampler checks both before reading the raster.
- **Approved historical lineage:** the original 56 records must remain exactly
  equal to the base manifest authenticated by its independently configured
  SHA-256.
- **Declared source identity:** added records must declare GLO-30 2024_1 and a
  CDSE `s3://eodata/CCM/COP-DEM_GLO-30-DGED/SAR_DGE_30_A4AD/` locator whose
  grid ID, DEM/AUXFILES layer, and filename match the record's cell.
- **Actual geographic grid:** raster CRS, PixelIsPoint tag, dimensions, affine,
  orientation, spacing, and DEM/WBM alignment must match the DGED contract
  above.
- **Independent upstream authentication:** no authoritative per-object
  provider checksum is present for the eight additional 2024_1 cells. The
  manifest's locally computed SHA-256 does not prove Copernicus origin, and a
  source URI is acquisition provenance rather than cryptographic file
  authentication. No provider checksum is inferred or invented here. The
  added files still require verified CDSE acquisition/catalogue provenance
  during controlled preparation.

The same validator runs before G2 geographic index preparation, before halo
qualification sampling, and before runtime G2 sampler construction. A failure
therefore cannot produce a qualification result or enter geographic
preparation/enrichment.
