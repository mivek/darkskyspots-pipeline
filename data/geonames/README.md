# GeoNames country archives used by the naming measurement

The ZIP files in this directory are the official GeoNames national extracts,
downloaded from <https://download.geonames.org/export/dump/> on 2026-08-25.
They contain the 19-column `XX.txt` gazetteer and the upstream `readme.txt`.
The measurement script streams the text member directly; it does not extract
or modify the archives.

GeoNames data is distributed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
The report records the archive size and SHA-256 so a later measurement cannot
silently mix database versions.

| Archive | Bytes | SHA-256 |
|---|---:|---|
| `FR.zip` | 7295429 | `f39c60910f77bd8dec59ed6ee27a5e2550887b2a3adb3824ba576adb84f86c3c` |
| `ES.zip` | 3327985 | `4f488b79a54699b3d178878103052fa89af9b3ef1e1ec0be71d0eeda76b9202c` |
| `GB.zip` | 3638559 | `eaeab49c89415f5b3a11827c8922a830aadf9fed0b78076b30b5ba27bad25c70` |
