"""
Reads uploaded files into {"columns", "rows", "row_count", "format", "geometry"}.
Rows that are entirely empty are dropped, since Excel often leaves formatted
blank rows inside a sheet's used range.
"""
import json
import os

import pandas as pd


class ParseError(ValueError):
    pass


_CSV_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")


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
    df = df.dropna(how="all").astype(object)
    return df.where(pd.notnull(df), None)


def _read_csv(path, **kwargs):
    """Excel on Windows often exports cp1252, so fall back before giving up."""
    for encoding in _CSV_ENCODINGS:
        try:
            return pd.read_csv(path, encoding=encoding, **kwargs)
        except UnicodeDecodeError:
            continue
    raise ParseError("Could not decode this CSV. Save it as UTF-8 and try again.")


def _raw_header_duplicates(read_header_fn):
    """pandas quietly renames a repeated header ("name" to "name.1") while
    reading, so the collision has to be checked on the raw header row first."""
    try:
        raw = list(read_header_fn())
    except Exception:
        return []
    seen, dupes = set(), set()
    for c in raw:
        if c in seen:
            dupes.add(c)
        seen.add(c)
    return sorted(dupes)


def _parse_csv(path):
    dupes = _raw_header_duplicates(lambda: _read_csv(path, header=None, nrows=1, dtype=str).iloc[0].tolist())
    if dupes:
        raise ParseError(f"Duplicate column header(s): {', '.join(dupes)}. Rename them before uploading.")
    try:
        df = _read_csv(path, dtype=str, keep_default_na=True)
    except ParseError:
        raise
    except pd.errors.EmptyDataError:
        raise ParseError("File is empty.")
    except Exception as e:
        raise ParseError(f"Could not read this as CSV ({e}).")
    if df.shape[1] == 0:
        raise ParseError("No header row found, the first line should be column names.")
    df = _clean_rows(df)
    return {"columns": list(df.columns), "rows": df.to_dict(orient="records"),
            "row_count": len(df), "format": "csv", "geometry": None}


def _parse_excel(path):
    dupes = _raw_header_duplicates(lambda: pd.read_excel(path, sheet_name=0, header=None, nrows=1, dtype=str).iloc[0].tolist())
    if dupes:
        raise ParseError(f"Duplicate column header(s): {', '.join(dupes)}. Rename them before uploading.")
    try:
        df = pd.read_excel(path, sheet_name=0, dtype=str)
    except Exception as e:
        raise ParseError(f"Could not read this as an Excel file ({e}).")
    if df.shape[1] == 0:
        raise ParseError("First sheet's header row is empty.")
    df = _clean_rows(df)
    return {"columns": list(df.columns), "rows": df.to_dict(orient="records"),
            "row_count": len(df), "format": "xlsx", "geometry": None}


def _parse_geojson(path):
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise ParseError(f"Invalid JSON ({e}).")
    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list):
        raise ParseError("Not a GeoJSON FeatureCollection: there is no 'features' array.")

    rows = [dict(f.get("properties") or {}) for f in features]
    columns = []
    for row in rows:  # union across features, since properties can differ between them
        for key in row:
            if key not in columns:
                columns.append(key)
    return {"columns": columns, "rows": rows, "row_count": len(rows), "format": "geojson",
            "geometry": [f.get("geometry") for f in features]}


def save_parsed_payload(upload_dir, file_id, payload):
    out_path = os.path.join(upload_dir, f"{file_id}.parsed.json")
    with open(out_path, "w", encoding="utf-8") as f:
        # default=str so database dates, decimals and UUIDs do not break the dump
        json.dump(payload, f, default=str)
    return out_path


def load_parsed_payload(upload_dir, file_id):
    path = os.path.join(upload_dir, f"{file_id}.parsed.json")
    if not os.path.exists(path):
        raise ParseError(f"No cached parsed data for file {file_id}. Re-upload it.")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
