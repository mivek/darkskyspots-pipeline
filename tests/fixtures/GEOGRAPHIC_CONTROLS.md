# Geographic review controls

`geographic_review_controls.json` contains real VIIRS pixel-centre coordinates
and existing enriched place names from the A/D review at data reference
`1502e353ef7f88aa63a95deb5678e7af33b9b028`. Names originate from GeoNames
([attribution and CC BY terms](https://www.geonames.org/)). Coordinate inputs
originate from NASA VIIRS; review observations were authored for this project.

The file includes no OSM vector geometry, image pixels or imagery-derived
polygons. It is a validation corpus, never a production exclusion list.
The other new geographic unit-test polygons are synthetic.

The follow-up adds `46.5771_-1.3604` near Chaillé-sous-les-Ormeaux as a
twenty-fifth control. IGN BD TOPO river object `SURF_EAU0000000017424024`,
OpenStreetMap river object `18053436`, and separately inspected Esri/IGN
images support a probable-water conclusion at that centre. Its expected
exclusion is a regression requirement, not an implemented spot-ID rule.
The unreviewed remainder of the river is not promoted to a water exclusion.
IGN source metadata is attributed to IGN under Licence Ouverte 2.0; source
geometries and imagery remain outside the public code repository.
