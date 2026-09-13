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
    df = df.dropna(how="all")
    df = df.where(pd.notnull(df), None)
    return df


def _raw_header_duplicates(path, read_header_fn):
    """pandas silently renames a repeated header ("name" -> "name.1")
    during the real read, so checking df.columns afterwards can never see
    the collision that actually happened in the file. Read just the raw
    header row first and check it before pandas gets a chance to paper
    over it."""
    try:
        raw_columns = list(read_header_fn())
    except Exception:
        return []
    seen = set()
    dupes = set()
    for c in raw_columns:
        if c in seen:
            dupes.add(c)
        seen.add(c)
    return sorted(dupes)


def _parse_csv(path):
    dupes = _raw_header_duplicates(path, lambda: pd.read_csv(path, header=None, nrows=1, dtype=str).iloc[0].tolist())
    if dupes:
        raise ParseError(f"Duplicate column header(s): {', '.join(dupes)}. Rename them before uploading.")

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
    dupes = _raw_header_duplicates(path, lambda: pd.read_excel(path, sheet_name=0, header=None, nrows=1, dtype=str).iloc[0].tolist())
    if dupes:
        raise ParseError(f"Duplicate column header(s): {', '.join(dupes)}. Rename them before uploading.")

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
