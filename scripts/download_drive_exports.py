"""
Downloads the GEE export CSVs (submitted by scripts/extract_gee_features.py) from the
user's Google Drive "EE_Exports" folder into data/processed/, using the same OAuth
credentials Earth Engine already saved locally (the auth flow requests Drive scope
specifically to support this).

Usage:
    python scripts/download_drive_exports.py
    python scripts/download_drive_exports.py --districts Saptari,Kathmandu
"""
import argparse
import io
import json
from pathlib import Path

import ee.oauth as oauth
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"
DRIVE_FOLDER_NAME = "EE_Exports"


def get_drive_service():
    with open(oauth.get_credentials_path()) as f:
        creds_info = json.load(f)
    creds = Credentials(
        token=None,
        refresh_token=creds_info["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=oauth.CLIENT_ID,
        client_secret=oauth.CLIENT_SECRET,
        scopes=creds_info["scopes"],
    )
    return build("drive", "v3", credentials=creds)


def find_export_folder(drive):
    results = drive.files().list(
        q=f"name = '{DRIVE_FOLDER_NAME}' and mimeType = 'application/vnd.google-apps.folder'",
        fields="files(id, name)",
    ).execute()
    files = results.get("files", [])
    if not files:
        raise SystemExit(f"No Drive folder named '{DRIVE_FOLDER_NAME}' found.")
    return files[0]["id"]


def list_csvs(drive, folder_id):
    files = []
    page_token = None
    while True:
        results = drive.files().list(
            q=f"'{folder_id}' in parents and name contains '.csv'",
            fields="nextPageToken, files(id, name, size, modifiedTime)",
            pageSize=1000,
            pageToken=page_token,
        ).execute()
        files.extend(results.get("files", []))
        page_token = results.get("nextPageToken")
        if not page_token:
            break
    return files


def download_file(drive, file_id, dest: Path):
    request = drive.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _status, done = downloader.next_chunk()
    dest.write_bytes(buf.getvalue())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--districts", help="Comma-separated subset of districts to download (default: all)")
    parser.add_argument("--force", action="store_true", help="Re-download even if the local file already exists")
    parser.add_argument("--delete-after", action="store_true", help="Delete each file from Drive after a successful local download (frees Drive storage quota)")
    args = parser.parse_args()

    wanted = {d.strip() for d in args.districts.split(",")} if args.districts else None

    drive = get_drive_service()
    folder_id = find_export_folder(drive)
    csvs = list_csvs(drive, folder_id)
    print(f"Found {len(csvs)} CSV(s) in Drive folder '{DRIVE_FOLDER_NAME}'")

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    downloaded, skipped, deleted = 0, 0, 0
    for f in csvs:
        name = f["name"]
        if wanted is not None:
            district = name.split("_Block_")[0]
            if district not in wanted:
                continue

        dest = PROCESSED_DIR / name
        expected_size = int(f.get("size", 0))
        already_local = dest.exists() and dest.stat().st_size == expected_size

        if already_local and not args.force:
            skipped += 1
        else:
            size_mb = expected_size / 1e6
            print(f"Downloading {name} ({size_mb:.1f} MB)...")
            download_file(drive, f["id"], dest)
            downloaded += 1

        if args.delete_after and dest.exists() and dest.stat().st_size == expected_size:
            drive.files().delete(fileId=f["id"]).execute()
            deleted += 1

    print(f"Downloaded {downloaded} file(s), skipped {skipped} already-present file(s), deleted {deleted} file(s) from Drive.")


if __name__ == "__main__":
    main()
