# Natural Earth 1:10m

This directory contains the Natural Earth v5.1.1 `admin_0_countries` and
`land` layers, versioned so pipeline runs do not depend on network state.
The files are derived from the official release at
https://github.com/nvkelso/natural-earth-vector/releases/tag/v5.1.1 and are
public-domain data.  The `.VERSION.txt` files and SHA-256 values below pin the
source components.

```
ne_10m_admin_0_countries.shp  7ce119ef6342e43cff7c0c3004e0911ab7ec1988a14734372031d2012180e7bc
ne_10m_admin_0_countries.shx  ca19ec112d054c77bc8f7ac00e3b110d5dff32cc9bcf4cd1b8b66bdd0f611d32
ne_10m_admin_0_countries.dbf  c5dbd3dd5fd7e2ef49051fc88562c03819e8ea63a382642df6eadd1243bf4b49
ne_10m_land.shp               4cad3a49bc75c1a4c2f3d7efae04f2f8e63151c96764b2658effabf524331fa6
ne_10m_land.shx               f3b02a190c3020eeafd0f48de982619826b57ecd853cbc68e4fc049652891a04
ne_10m_land.dbf               e422520372e65c6fb8220888580e5331d04699bcd71c27e53ad42e241a531cbd
```

Small islands represented below the raster mesh spacing can still have no
candidate; this is a limitation of the sampling grid, not a coastline
tolerance. No buffer is applied to the land geometry.
