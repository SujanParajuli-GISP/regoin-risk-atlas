# End-to-End Pipeline Tutorial

This walks through the full pipeline behind the Nepal Hydro-Climatic Risk Atlas: satellite
data extraction → risk scoring → the Streamlit dashboard → pushing to GitHub → deploying to
Streamlit Community Cloud. It reflects the actual working pipeline in this repo (`scripts/`),
not the original one-off Saptari notebooks.

## Pipeline overview

```
data/boundaries/*.geojson  (Nepal district + block boundaries, already committed)
        │
        ▼
scripts/extract_gee_features.py   ── submits GEE export tasks (Sentinel-2 NDVI, CHIRPS
        │                             Rainfall, MODIS ET) per district, to Google Drive
        ▼
scripts/download_drive_exports.py ── pulls the completed CSVs from Drive into
        │                             data/processed/ (and can free Drive space as it goes)
        ▼
scripts/build_risk_atlas.py       ── feature engineering + compound risk scoring →
        │                             data/final_risk_atlas_nepal.geojson
        ▼
frontend/app.py                   ── Streamlit dashboard reads that GeoJSON
```

`data/processed/` (the raw CSVs, ~9GB for all of Nepal) is **not** committed to git — only the
final, compact `data/final_risk_atlas_nepal.geojson` (~10MB) is, since that's all the deployed
app needs.

## Prerequisites

- Python 3.10+
- A [Google Earth Engine](https://earthengine.google.com) account with a registered Cloud
  project (free, noncommercial tier is fine — see the quota note below)
- ~10GB of free space in your Google Drive (temporary, for GEE exports — you'll be deleting
  files as you go, see Step 3)
- A GitHub account + repo
- A [Streamlit Community Cloud](https://share.streamlit.io) account (sign in with GitHub)

## Step 1 — Environment setup

```bash
git clone https://github.com/<you>/regoin-risk-atlas.git
cd regoin-risk-atlas
python -m pip install -r requirements.txt
```

## Step 2 — (Optional) Regenerate boundaries from raw shapefiles

`data/boundaries/districts_nepal_gee.geojson`, `blocks_nepal_gee.geojson`, and
`blocks_nepal_with_district.geojson` are already committed (simplified, ~1.8–15MB each) and
cover all 77 districts / 6,803 blocks — you normally don't need to touch this step.

Only redo it if you're starting from a different/updated source shapefile:

1. Run `notebooks/Step_01_prepare_boundaries.ipynb` to produce
   `data/boundaries/districts_nepal_gee.geojson` from the raw shapefile.
2. Run `python scripts/join_blocks_to_districts.py` to spatially join blocks to districts
   (centroid-in-polygon, with a nearest-neighbor fallback for edge cases), producing
   `data/boundaries/blocks_nepal_with_district.geojson`.
3. Run `python scripts/simplify_boundaries.py` to shrink the geometries before committing
   (drops ~110MB files to ~15MB with no visible loss of detail at map zoom levels).

## Step 3 — Authenticate Earth Engine

```bash
python -c "import ee; ee.Authenticate()"
```

This opens a browser OAuth flow (or prints a URL if no local browser is available). Once
authenticated, set your project ID in `scripts/extract_gee_features.py` (`GEE_PROJECT`
constant near the top) to your own Cloud project — find it at
[code.earthengine.google.com](https://code.earthengine.google.com) under your account, or in
the Google Cloud Console.

**Noncommercial tier quota**: Earth Engine's free tier has a compute quota. Submitting all 77
districts at once (231 tasks) can exceed it partway through, which silently nulls out results
for the affected districts rather than failing the task outright (we hit this — it corrupted
NDVI for ~30 districts, discoverable only by checking whether the exported CSV has a `mean`
column at all). If that happens, wait for the quota to reset, then resubmit just the affected
districts/variable:

```bash
python scripts/extract_gee_features.py --districts Jhapa,Banke,... --variables S2_NDVI
```

## Step 4 — Extract satellite features

```bash
# See what districts are available
python scripts/extract_gee_features.py --list-districts

# Validate on a small batch first (cheaper to catch bugs early)
python scripts/extract_gee_features.py --districts Saptari,Kathmandu,Manang

# Check on progress
python scripts/extract_gee_features.py --status

# Once validated, run the rest
python scripts/extract_gee_features.py --all
```

Each district submits 3 export tasks (Sentinel-2 NDVI, CHIRPS Rainfall, MODIS ET) to a
`EE_Exports` folder in your Google Drive. These run on Google's servers — you don't need to
keep anything running locally while they process (this can take from minutes to a couple of
hours for the full country, depending on GEE load).

## Step 5 — Download results (and manage Drive storage)

Google Drive's free tier is often too small to hold all 231 exports at once. Rather than
waiting for everything to finish, download completed files as they land and free the space
immediately:

```bash
python scripts/download_drive_exports.py --delete-after
```

Run this on a loop (e.g. every few minutes) while extraction is in progress. It downloads
whatever's newly completed into `data/processed/` and deletes it from Drive right after — safe,
since it verifies the local file size matches before deleting the remote copy.

## Step 6 — Build the risk atlas

```bash
python scripts/build_risk_atlas.py
```

This reads every complete `{District}_Block_{S2_NDVI,Rainfall,ET}.csv` triplet in
`data/processed/`, runs the feature engineering (rolling rainfall/NDVI windows, the
flood-pressure and groundwater-stress formulas), normalizes scores **nationally** so
"Critical" means top-20% risk across all processed districts (not just within one), and writes
`data/final_risk_atlas_nepal.geojson`.

You can run this against a partial set of districts at any time — it just processes whatever's
in `data/processed/`, so you can ship an app with 45 districts today and rebuild with more
later as extraction finishes.

## Step 7 — Run the app locally

```bash
streamlit run frontend/app.py
```

Streamlit caches `load_data()` per-process — if you rebuild `final_risk_atlas_nepal.geojson`
while the app is already running, restart the `streamlit run` process to pick up the new file
(reloading the browser page alone won't do it).

## Step 8 — Push to GitHub without the big data

The repo's `.gitignore` already excludes `data/processed/` (~9GB of raw CSVs — not needed by
the app, regenerable via Steps 4–5) and the raw source shapefiles (one-time manual inputs).
Only the small, derived boundary/atlas GeoJSONs are committed.

```bash
git add -A
git status   # sanity-check nothing under data/processed/ or *.zip/*.shp is staged
git commit -m "Your message"
git push
```

If you're forking this repo and it's your first push, replace the `origin` remote with your
own repo URL first (`git remote set-url origin <your-repo-url>`).

## Step 9 — Deploy to Streamlit Community Cloud

1. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.
2. Click **New app**.
3. Pick your repo, the `main` branch, and set the main file path to `frontend/app.py`.
4. Under **Advanced settings**, you generally don't need any secrets for this app (it reads a
   static GeoJSON, not live GEE data) — leave that blank unless you've added your own API keys.
5. Click **Deploy**. First boot takes a minute or two (installing `requirements.txt`,
   loading the ~10MB GeoJSON); subsequent visits are fast thanks to `@st.cache_data`.
6. Every future `git push` to the connected branch triggers an automatic redeploy — no manual
   step needed.

### Troubleshooting

- **App shows only Saptari, no district selector**: `final_risk_atlas_nepal.geojson` isn't in
  the repo/wasn't found — the app falls back to the original Saptari-only file. Confirm it's
  committed (`git ls-files data/final_risk_atlas_nepal.geojson`) and under the deploy platform's
  file size limits.
  Community Cloud, ~10MB is trivial.
- **GEE "Not enough space in Google Drive" errors mid-extraction**: run
  `scripts/download_drive_exports.py --delete-after` to free space, then resubmit just the
  failed district/variable combos (see the `--variables` flag in Step 3).
- **A district's NDVI is oddly all "Low" risk / missing**: check if its `_Block_S2_NDVI.csv`
  is missing the `mean` column entirely — either genuine persistent cloud/snow cover (e.g.
  high-altitude districts like Manang) or a GEE compute-quota hiccup (see Step 3). The build
  script logs a warning for each case; the affected district is simply excluded from that atlas
  build until re-extracted.
