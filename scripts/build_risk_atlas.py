"""
Generalizes notebooks/Step_03_merge_features_processing_machine_learning_and_final_outputs.ipynb
(hardcoded to Saptari) into a script that processes every district's exported CSVs in
data/processed/ and produces one nationwide data/final_risk_atlas_nepal.geojson.

Expects, per district, three CSVs from scripts/extract_gee_features.py:
    data/processed/{District}_Block_S2_NDVI.csv
    data/processed/{District}_Block_Rainfall.csv
    data/processed/{District}_Block_ET.csv

block_id is globally unique across all of Nepal (assigned in
data/boundaries/blocks_nepal_gee.geojson), so districts are simply concatenated --
no per-district ID remapping is needed (unlike the original notebook's local
block_uid indirection, which existed only because it processed one district at a time).

Feature engineering, the flood_pressure / gw_stress_index formulas, and the
compound-risk methodology are unchanged from the original notebook. The RF+SHAP
explainability cells are NOT reproduced here since they are exploratory only and
never feed the exported columns the app reads.

Usage:
    python scripts/build_risk_atlas.py
    python scripts/build_risk_atlas.py --districts Saptari,Kathmandu
"""
import argparse
import re
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"
BLOCKS_PATH = ROOT / "data" / "boundaries" / "blocks_nepal_with_district.geojson"
OUT_PATH = ROOT / "data" / "final_risk_atlas_nepal.geojson"

FILE_RE = re.compile(r"^(?P<district>.+)_Block_(?P<variable>S2_NDVI|Rainfall|ET)\.csv$")


def discover_districts() -> dict[str, dict[str, Path]]:
    """Map district name -> {"S2_NDVI"|"Rainfall"|"ET": csv path} for complete triplets."""
    found: dict[str, dict[str, Path]] = {}
    for path in PROCESSED_DIR.glob("*.csv"):
        m = FILE_RE.match(path.name)
        if not m:
            continue
        found.setdefault(m["district"], {})[m["variable"]] = path

    complete = {d: v for d, v in found.items() if {"S2_NDVI", "Rainfall", "ET"} <= v.keys()}
    incomplete = set(found) - set(complete)
    if incomplete:
        print(f"Skipping {len(incomplete)} district(s) with incomplete CSV triplets: {sorted(incomplete)}")
    return complete


def _read_variable_csv(path: Path, column: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "mean" not in df.columns:
        # GEE drops the reduced property from the CSV entirely when every
        # feature/image pair was null (e.g. persistent cloud/snow cover over
        # high-altitude districts leaves Sentinel-2 with no valid pixels).
        print(f"  warning: {path.name} has no 'mean' column (likely no valid pixels for any block/date) - treating {column} as all-missing")
        df["mean"] = np.nan
    return df.rename(columns={"mean": column})[["block_id", "year", "month", column]]


def load_district(district: str, files: dict[str, Path]) -> pd.DataFrame:
    ndvi = _read_variable_csv(files["S2_NDVI"], "NDVI")
    rain = _read_variable_csv(files["Rainfall"], "Rainfall")
    et = _read_variable_csv(files["ET"], "ET")

    ndvi_monthly = ndvi.groupby(["block_id", "year", "month"], as_index=False).agg(NDVI=("NDVI", "mean"))
    et_monthly = et.groupby(["block_id", "year", "month"], as_index=False).agg(ET=("ET", "mean"))

    df = (
        ndvi_monthly.merge(rain, on=["block_id", "year", "month"], how="left")
        .merge(et_monthly, on=["block_id", "year", "month"], how="left")
    )
    return df


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df[df["year"] >= 2021].reset_index(drop=True)
    df = df.sort_values(["block_id", "year", "month"]).reset_index(drop=True)

    g = df.groupby("block_id")

    df["rain_1m"] = df["Rainfall"]
    df["rain_3m"] = g["Rainfall"].rolling(3).sum().reset_index(0, drop=True)
    df["rain_6m"] = g["Rainfall"].rolling(6).sum().reset_index(0, drop=True)
    df["rain_anomaly"] = df["Rainfall"] - df.groupby(["block_id", "month"])["Rainfall"].transform("mean")
    df["ndvi_change"] = g["NDVI"].diff()
    df["ndvi_anomaly"] = df["NDVI"] - df.groupby(["block_id", "month"])["NDVI"].transform("mean")
    df["water_surplus"] = df["Rainfall"] - df["ET"]

    df["flood_pressure"] = (
        0.4 * df["rain_3m"] + 0.3 * df["rain_anomaly"] + 0.2 * df["water_surplus"] - 0.1 * df["ndvi_anomaly"]
    )

    df["et_rain_ratio"] = df["ET"] / (df["Rainfall"] + 1e-3)
    df["recharge_deficit"] = df["Rainfall"] - df["ET"]
    df["et_3m"] = g["ET"].rolling(3).mean().reset_index(0, drop=True)
    df["recharge_deficit_6m"] = df.groupby("block_id")["recharge_deficit"].rolling(6).sum().reset_index(0, drop=True)
    df["ndvi_et_ratio"] = df["NDVI"] / (df["ET"] + 1e-3)
    df["log_et_rain_ratio"] = np.log1p(df["et_rain_ratio"])

    df["gw_stress_index"] = (
        0.35 * df["log_et_rain_ratio"]
        + 0.30 * (-df["recharge_deficit"])
        + 0.20 * df["et_3m"]
        - 0.15 * df["ndvi_anomaly"]
    )

    return df.dropna().reset_index(drop=True)


def calc_slope(group: pd.DataFrame) -> float:
    if len(group) < 2:
        return 0.0
    slope, _intercept = np.polyfit(group["year"], group["gw_stress_index"], deg=1)
    return slope


def classify_trend(slope: float) -> str:
    if slope > 2.0:
        return "Rapid Degradation"
    elif slope > 0.5:
        return "Worsening"
    elif slope < -0.5:
        return "Improving"
    return "Stable"


def normalize(series: pd.Series) -> pd.Series:
    return (series - series.min()) / (series.max() - series.min())


def classify_compound(score: float, q80: float, q60: float, q40: float) -> str:
    if score > q80:
        return "Critical"
    elif score > q60:
        return "High"
    elif score > q40:
        return "Moderate"
    return "Low"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--districts", help="Comma-separated subset of districts to process (default: all discovered)")
    args = parser.parse_args()

    districts = discover_districts()
    if args.districts:
        wanted = {d.strip() for d in args.districts.split(",")}
        districts = {d: v for d, v in districts.items() if d in wanted}
        missing = wanted - districts.keys()
        if missing:
            print(f"Requested districts not found in data/processed/: {sorted(missing)}")

    if not districts:
        raise SystemExit("No complete district CSV triplets found in data/processed/. Run scripts/extract_gee_features.py first and download the results from Google Drive.")

    print(f"Processing {len(districts)} district(s): {sorted(districts)}")

    frames = [load_district(d, files) for d, files in districts.items()]
    df = pd.concat(frames, ignore_index=True)
    df = engineer_features(df)
    print(f"Feature rows after engineering: {len(df)}")

    # --- Block-level aggregation ---
    scores = df.groupby("block_id", as_index=False).agg(
        Flood_Risk_Score=("flood_pressure", "mean"),
        GW_Stress_Score=("gw_stress_index", "mean"),
    )

    # --- National normalization + 4-tier compound risk classification ---
    scores["Flood_Norm"] = normalize(scores["Flood_Risk_Score"])
    scores["GW_Norm"] = normalize(scores["GW_Stress_Score"])
    scores["Compound_Score"] = 0.5 * scores["Flood_Norm"] + 0.5 * scores["GW_Norm"]

    q80, q60, q40 = scores["Compound_Score"].quantile([0.80, 0.60, 0.40])
    scores["Risk_Category"] = scores["Compound_Score"].apply(lambda x: classify_compound(x, q80, q60, q40))

    # --- Trend analysis (annual OLS slope of gw_stress_index) ---
    annual = df.groupby(["block_id", "year"])["gw_stress_index"].mean().reset_index()
    trend = annual.groupby("block_id").apply(calc_slope, include_groups=False).reset_index(name="Degradation_Rate")
    trend["Trend_Status"] = trend["Degradation_Rate"].apply(classify_trend)

    scores = scores.merge(trend, on="block_id", how="left")

    # --- Join to geometry + District + block name ---
    blocks = gpd.read_file(BLOCKS_PATH)
    blocks["block_id"] = blocks["block_id"].astype(int)
    blocks = blocks.rename(columns={"gapa_napa": "block_name"})[["block_id", "District", "block_name", "geometry"]]

    final_gdf = blocks.merge(scores, on="block_id", how="inner")
    print(f"Final blocks with computed risk scores: {len(final_gdf)} across {final_gdf['District'].nunique()} district(s)")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if OUT_PATH.exists():
        OUT_PATH.unlink()
    final_gdf.to_file(OUT_PATH, driver="GeoJSON")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
