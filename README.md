# darkskyspots-pipeline

Local Python batch pipeline that transforms VIIRS radiance GeoTIFFs into per-tile JSON spot files for the ["ciel nocturne"](../app-dark-sky) mobile app.

**Status:** 🚧 Design approved, planning in progress. Implementation tracked on the `feature/dark-sky-pipeline-mvp` worktree.

## What this is

- **Input:** VIIRS radiance GeoTIFF (NASA Black Marble / lightpollutionmap raw) + OpenStreetMap data
- **Processing:** ALR (All-sky Light pollution Ratio) via the [`nightskyquality`](https://github.com/mivek/nightskyquality) Python package (pinned at `v1.0.0`)
- **Output:** per-tile JSON files (`spots/<tileId>.json`) written locally, then pushed by step 7 to a separate data repo consumed by the app
- **Frequency:** ~1×/year (annual VIIRS composite)
- **Runtime:** local script on a Mini PC; **not a server, not an API**

## What this is not

- Not coupled to the app's source code — the only contract is the JSON schema defined in `app-dark-sky/spec-technique.md`
- Not a permanent process — a single `python run.py --year YYYY --region <region>` invocation
- Not a vendored copy of `nightskyquality` — that fork is installed as a pip dependency from a Git tag
- Not the data repo — `/spots/` is local staging; the data repo (consumed by the app) is a separate clone managed by step 7

## Repository layout

```
.
├── docs/
│   ├── designs/   # approved design documents
│   └── plans/     # implementation plans (one per feature/worktree)
├── src/           # pipeline modules (created in the implementation worktree)
├── run.py         # orchestrator entrypoint (created in the implementation worktree)
├── requirements.txt
├── validation/    # hand-curated control points for the §6 validation procedure
└── README.md
```

## Documentation

- **[Design doc](docs/designs/2026-06-30-darkskyspots-pipeline.md)** — the approved design, including the user's spec verbatim and the decisions taken during the design phase
- Implementation plan — coming soon, on the `feature/dark-sky-pipeline-mvp` worktree

## Credits

- **ALR method:** Duriscoe, D. et al. (2018). *A simplified model of all-sky artificial sky glow derived from VIIRS Day/Night band data.* J. Quant. Spectrosc. Radiat. Transf. 214, 133–145. Implemented in Python by Katy Abbott (NPS) and maintained at [github.com/mivek/nightskyquality](https://github.com/mivek/nightskyquality) (MIT).
- **VIIRS radiance data:** NASA Black Marble products (VNP46A4 / VJ146A4) — CC0.
- **Light pollution map redistribution:** Jurij Stare, [lightpollutionmap.info](https://www.lightpollutionmap.info).
- **Place names and administrative boundaries:** [OpenStreetMap](https://www.openstreetmap.org) contributors (ODbL).
