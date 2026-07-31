"""
Assign a District to every block in data/boundaries/blocks_nepal_gee.geojson by
spatial-joining block centroids against data/boundaries/districts_nepal_gee.geojson.

Output: data/boundaries/blocks_nepal_with_district.geojson (adds a "District" column,
title-cased, e.g. "Saptari").
"""
from pathlib import Path

import geopandas as gpd

ROOT = Path(__file__).resolve().parent.parent
BLOCKS_PATH = ROOT / "data" / "boundaries" / "blocks_nepal_gee.geojson"
DISTRICTS_PATH = ROOT / "data" / "boundaries" / "districts_nepal_gee.geojson"
OUT_PATH = ROOT / "data" / "boundaries" / "blocks_nepal_with_district.geojson"


def main():
    blocks = gpd.read_file(BLOCKS_PATH)
    districts = gpd.read_file(DISTRICTS_PATH)[["FIRST_DIST", "geometry"]]

    centroids = blocks.copy()
    centroids["geometry"] = blocks.geometry.centroid

    joined = gpd.sjoin(centroids, districts, how="left", predicate="within")[
        ["block_id", "FIRST_DIST"]
    ]

    missing = joined["FIRST_DIST"].isna()
    if missing.any():
        # Centroid can fall just outside a district polygon due to simplification.
        # Fall back to nearest district for those blocks.
        unmatched = centroids.loc[missing, ["block_id", "geometry"]]
        nearest = gpd.sjoin_nearest(unmatched, districts, how="left")[
            ["block_id", "FIRST_DIST"]
        ]
        joined = joined.set_index("block_id")
        joined.update(nearest.set_index("block_id"))
        joined = joined.reset_index()

    blocks = blocks.merge(joined, on="block_id", how="left")
    blocks["District"] = blocks["FIRST_DIST"].str.title()
    blocks = blocks.drop(columns=["FIRST_DIST"])

    still_missing = blocks["District"].isna().sum()
    print(f"Total blocks: {len(blocks)}")
    print(f"Unmatched after nearest-fallback: {still_missing}")
    print(f"Districts represented: {blocks['District'].nunique()}")
    print(blocks["District"].value_counts().head(10))

    blocks.to_file(OUT_PATH, driver="GeoJSON")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
