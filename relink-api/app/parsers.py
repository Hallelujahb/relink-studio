"""
Server-side equivalent of the frontend's parseUploadedFile(). The frontend
version only inspects a file to preview columns/row count; this version
also returns the full row data, since the matching engine needs it.

Known Excel quirk handled here: naive `sheet_to_json(sheet, {header: 1})`-
style parsing counts trailing blank rows that Excel often leaves inside a
sheet's used range. Any row that is entirely empty/NaN is dropped before
counting or matching against it.
"""
import json
import os

import pandas as pd


class ParseError(ValueError):
    pass


def parse_file(path, original_filename):
    ext = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else ""

    if ext == "csv":
        return _parse_csv(path)
    if ext in ("xlsx", "xls"):
        return _parse_excel(path)
    if ext in ("geojson", "json"):
        return _parse_geojson(path)
    raise ParseError(f"Unsupported file type: .{ext}. Use .csv, .xlsx, .xls, .geojson, or .json.")


def _clean_rows(df):
    # Drop fully-blank rows (the trailing-empty-row bug) and normalize NaN -> None.
    df = df.dropna(how="all")
    df = df.where(pd.notnull(df), None)
    return df


def _parse_csv(path):
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=True)
    except pd.errors.EmptyDataError:
        raise ParseError("File is empty.")
    except Exception as e:
        raise ParseError(f"Could not read this as CSV ({e}).")

    if df.shape[1] == 0:
        raise ParseError("No header row found, the first line should be column names.")

    df = _clean_rows(df)
    columns = list(df.columns)
    rows = df.to_dict(orient="records")
    return {"columns": columns, "rows": rows, "row_count": len(rows), "format": "csv", "geometry": None}


def _parse_excel(path):
    try:
        df = pd.read_excel(path, sheet_name=0, dtype=str)
    except Exception as e:
        raise ParseError(f"Could not read this as an Excel file ({e}).")

    if df.shape[1] == 0:
        raise ParseError("First sheet's header row is empty.")

    df = _clean_rows(df)
    columns = list(df.columns)
    rows = df.to_dict(orient="records")
    return {"columns": columns, "rows": rows, "row_count": len(rows), "format": "xlsx", "geometry": None}


def _parse_geojson(path):
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise ParseError(f"Invalid JSON ({e}).")

    features = data.get("features")
    if not isinstance(features, list):
        raise ParseError("Not a valid GeoJSON FeatureCollection, missing a 'features' array.")

    rows = [dict(f.get("properties") or {}) for f in features]
    columns = list(rows[0].keys()) if rows else []
    geometry = [f.get("geometry") for f in features]
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "format": "geojson",
        "geometry": geometry,
    }


def save_parsed_payload(upload_dir, file_id, payload):
    """Cache full parsed rows + geometry to disk, keyed by file_id, so /run
    doesn't need to re-parse or hold everything in memory between requests."""
    out_path = os.path.join(upload_dir, f"{file_id}.parsed.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return out_path


def load_parsed_payload(upload_dir, file_id):
    path = os.path.join(upload_dir, f"{file_id}.parsed.json")
    if not os.path.exists(path):
        raise ParseError(f"No cached parsed data for file {file_id}. Re-upload it.")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
