# GeoNames filtered extracts

The runtime reads only the UTF-8 TSV files in `extracts/`. The national ZIP
archives are local-only re-extraction sources in `sources/` and are ignored by
Git. They are downloaded from the official [GeoNames dump directory](https://download.geonames.org/export/dump/).

Each TSV has this stable seven-column schema:

```text
geonameid  name  latitude  longitude  feature_class  feature_code  country_code
```

The extraction is filtered by feature code only; it never uses a region bbox.
The runtime applies the region bbox plus its 40 km ordinary-feature margin,
while ADM1 and ADM2 rows remain available as whole-country fallbacks. Unicode
names are copied unchanged, including accents and Gaelic spellings.

`manifest.yaml` records the source URL, local source path, download date,
source SHA-256, extract SHA-256 and the exact sorted code list. The runtime
fails before raster work if an extract is missing, its hash differs, or the
current code list differs from the manifest. Recreate the files with:

```bash
python tools/extract_geonames.py \
  --geonames-dir data/geonames \
  --country FR --country ES --country GB --country IE \
  --downloaded-at FR=2026-08-25 \
  --downloaded-at ES=2026-08-25 \
  --downloaded-at GB=2026-08-25 \
  --downloaded-at IE=2026-08-27
```

Sans `--code`, l’outil applique la liste canonique du runtime. Une liste
personnalisée peut être fournie avec `--code` (répétable) ou `--codes` ; `ADM1`
et `ADM2` sont ajoutés pour le fallback administratif, et la liste effective
triée est celle inscrite dans `codes_applied`.

GeoNames data is distributed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
The manifest is the provenance record; the source archive hashes currently
used are:

| Archive | Bytes | SHA-256 |
|---|---:|---|
| `FR.zip` | 7295429 | `f39c60910f77bd8dec59ed6ee27a5e2550887b2a3adb3824ba576adb84f86c3c` |
| `ES.zip` | 3327985 | `4f488b79a54699b3d178878103052fa89af9b3ef1e1ec0be71d0eeda76b9202c` |
| `GB.zip` | 3638559 | `eaeab49c89415f5b3a11827c8922a830aadf9fed0b78076b30b5ba27bad25c70` |
| `IE.zip` | 817542 | `145fe7e1d3f5d172fe0d2a77f71e41f30e4096c17644a0879d72c4c682f351d5` |
