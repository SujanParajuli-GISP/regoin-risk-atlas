"""
Submit Google Earth Engine export tasks (Sentinel-2 NDVI, CHIRPS Rainfall, MODIS ET)
for one or more Nepal districts, one district's blocks per Export.table.toDrive task.

Generalizes notebooks/Step_02_extraction_gee_python.ipynb (which was hardcoded to
Saptari) to run per-district over data/boundaries/blocks_nepal_with_district.geojson
(produced by scripts/join_blocks_to_districts.py).

Usage:
    python scripts/extract_gee_features.py --list-districts
    python scripts/extract_gee_features.py --districts Saptari,Kathmandu,Jhapa
    python scripts/extract_gee_features.py --all
    python scripts/extract_gee_features.py --status
"""
import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd

ROOT = Path(__file__).resolve().parent.parent
BLOCKS_PATH = ROOT / "data" / "boundaries" / "blocks_nepal_with_district.geojson"

START_DATE = "2019-01-01"
END_DATE = "2024-12-31"
DRIVE_FOLDER = "EE_Exports"
GEE_PROJECT = "ee-sujanparajuli2070"


def ee_init():
    import ee

    try:
        ee.Initialize(project=GEE_PROJECT)
    except Exception:
        ee.Authenticate()
        ee.Initialize(project=GEE_PROJECT)
    return ee


def load_district_blocks(district: str) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(BLOCKS_PATH)
    subset = gdf[gdf["District"] == district]
    if subset.empty:
        raise ValueError(f"No blocks found for district '{district}'")
    return subset


def to_ee_feature_collection(ee, gdf: gpd.GeoDataFrame):
    reprojected = gdf.to_crs("EPSG:4326").copy()
    reprojected["geometry"] = reprojected.geometry.simplify(
        tolerance=0.001, preserve_topology=True
    )
    geo_json = json.loads(reprojected.to_json())
    return ee.FeatureCollection(geo_json)


def submit_district_tasks(ee, district: str, blocks_fc, dry_run: bool = False, variables: set[str] | None = None):
    """variables: subset of {"S2_NDVI", "Rainfall", "ET"} to submit; None = all."""
    variables = variables or {"S2_NDVI", "Rainfall", "ET"}
    tasks = []

    # --- Sentinel-2 NDVI ---
    if "S2_NDVI" in variables:
        def calc_s2_ndvi(img):
            ndvi = img.normalizedDifference(["B8", "B4"]).rename("NDVI")
            return ndvi.copyProperties(img, ["system:time_start"])

        s2 = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(blocks_fc)
            .filterDate(START_DATE, END_DATE)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 40))
            .map(calc_s2_ndvi)
        )

        def reduce_s2(img):
            date = ee.Date(img.get("system:time_start"))
            return img.reduceRegions(
                collection=blocks_fc, reducer=ee.Reducer.mean(), scale=10
            ).map(lambda f: f.set({"year": date.get("year"), "month": date.get("month")}))

        ndvi_table = s2.map(reduce_s2).flatten()
        tasks.append(
            ee.batch.Export.table.toDrive(
                collection=ndvi_table,
                folder=DRIVE_FOLDER,
                description=f"{district}_Block_S2_NDVI",
                fileFormat="CSV",
            )
        )

    # --- CHIRPS Rainfall ---
    if "Rainfall" in variables:
        chirps = (
            ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
            .filterBounds(blocks_fc)
            .filterDate(START_DATE, END_DATE)
        )
        years = ee.List.sequence(2019, 2024)
        months = ee.List.sequence(1, 12)

        def create_monthly_rain(y):
            def month_loop(m):
                start = ee.Date.fromYMD(y, m, 1)
                end = start.advance(1, "month")
                rain = chirps.filterDate(start, end).sum().rename("Rainfall")
                return rain.set({"year": y, "month": m, "system:time_start": start.millis()})

            return months.map(month_loop)

        monthly_rain = ee.ImageCollection(years.map(create_monthly_rain).flatten())

        def reduce_rain(img):
            return img.reduceRegions(
                collection=blocks_fc, reducer=ee.Reducer.mean(), scale=5000
            ).map(lambda f: f.set({"year": img.get("year"), "month": img.get("month")}))

        rain_table = monthly_rain.map(reduce_rain).flatten()
        tasks.append(
            ee.batch.Export.table.toDrive(
                collection=rain_table,
                folder=DRIVE_FOLDER,
                description=f"{district}_Block_Rainfall",
                fileFormat="CSV",
            )
        )

    # --- MODIS ET ---
    if "ET" in variables:
        def process_et(img):
            return img.multiply(0.1).rename("ET").copyProperties(img, ["system:time_start"])

        et_col = (
            ee.ImageCollection("MODIS/061/MOD16A2")
            .filterBounds(blocks_fc)
            .filterDate(START_DATE, END_DATE)
            .select("ET")
            .map(process_et)
        )

        def reduce_et(img):
            date = ee.Date(img.get("system:time_start"))
            return img.reduceRegions(
                collection=blocks_fc, reducer=ee.Reducer.mean(), scale=500
            ).map(lambda f: f.set({"year": date.get("year"), "month": date.get("month")}))

        et_table = et_col.map(reduce_et).flatten()
        tasks.append(
            ee.batch.Export.table.toDrive(
                collection=et_table,
                folder=DRIVE_FOLDER,
                description=f"{district}_Block_ET",
                fileFormat="CSV",
            )
        )

    if dry_run:
        print(f"[dry-run] {district}: would submit {len(tasks)} tasks")
        return

    for task in tasks:
        task.start()
    print(f"{district}: submitted {len(tasks)} tasks ({', '.join(t.config['description'] for t in tasks)})")


def print_status(ee):
    tasks = ee.data.getTaskList()
    if not tasks:
        print("No tasks found.")
        return
    by_state = {}
    for t in tasks:
        by_state.setdefault(t["state"], []).append(t["description"])
    for state, names in by_state.items():
        print(f"{state}: {len(names)}")
    print()
    for t in sorted(tasks, key=lambda x: x.get("start_timestamp_ms", 0), reverse=True)[:30]:
        print(f"  [{t['state']:>10}] {t['description']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--districts", help="Comma-separated district names (title case, e.g. Saptari,Kathmandu)")
    parser.add_argument("--all", action="store_true", help="Submit tasks for all districts")
    parser.add_argument("--list-districts", action="store_true", help="List available district names and exit")
    parser.add_argument("--status", action="store_true", help="Print current GEE task queue status and exit")
    parser.add_argument("--dry-run", action="store_true", help="Build tasks but don't submit them")
    parser.add_argument("--variables", help="Comma-separated subset of S2_NDVI,Rainfall,ET to (re)submit (default: all three)")
    args = parser.parse_args()
    variables = {v.strip() for v in args.variables.split(",")} if args.variables else None

    if args.list_districts:
        gdf = gpd.read_file(BLOCKS_PATH)
        for name in sorted(gdf["District"].unique()):
            print(name)
        return

    ee = ee_init()

    if args.status:
        print_status(ee)
        return

    if args.all:
        districts = sorted(gpd.read_file(BLOCKS_PATH)["District"].unique())
    elif args.districts:
        districts = [d.strip() for d in args.districts.split(",")]
    else:
        parser.error("Specify --districts, --all, --list-districts, or --status")
        return

    for district in districts:
        try:
            blocks = load_district_blocks(district)
            fc = to_ee_feature_collection(ee, blocks)
            submit_district_tasks(ee, district, fc, dry_run=args.dry_run, variables=variables)
        except Exception as e:
            print(f"{district}: FAILED - {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
