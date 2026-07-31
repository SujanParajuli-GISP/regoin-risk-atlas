"""
Simplify the committed boundary GeoJSONs so the repo stays small. The source
ward-level shapefiles are at survey precision (~110MB for 6,803 blocks),
which is far more detail than either GEE reduceRegions or the map display
needs. This overwrites the files in place at a coarser (but still visually
faithful) tolerance.

Run this once after regenerating boundaries from raw shapefiles (Step_01
notebook + join_blocks_to_districts.py), before committing.

Usage:
    python scripts/simplify_boundaries.py
"""
from pathlib import Path

import geopandas as gpd

ROOT = Path(__file__).resolve().parent.parent
BOUNDARIES_DIR = ROOT / "data" / "boundaries"
TOLERANCE = 0.0005  # degrees, ~50m at the equator

FILES = [
    BOUNDARIES_DIR / "districts_nepal_gee.geojson",
    BOUNDARIES_DIR / "blocks_nepal_gee.geojson",
    BOUNDARIES_DIR / "blocks_nepal_with_district.geojson",
]


def main():
    for path in FILES:
        if not path.exists():
            print(f"skip (not found): {path}")
            continue
        before_mb = path.stat().st_size / 1e6
        gdf = gpd.read_file(path)
        gdf["geometry"] = gdf.geometry.simplify(tolerance=TOLERANCE, preserve_topology=True)
        path.unlink()
        gdf.to_file(path, driver="GeoJSON")
        after_mb = path.stat().st_size / 1e6
        print(f"{path.name}: {before_mb:.1f}MB -> {after_mb:.1f}MB")


if __name__ == "__main__":
    main()
