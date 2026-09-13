"""
Data-quality profiling for an already-parsed dataset (the same
{"columns": [...], "rows": [...]} shape produced by app.parsers.parse_file).

Pure/local: no network calls, no external services. Runs entirely on
whatever's already been uploaded, so it fits ReLink's local-first model.
"""
import math
import re
from collections import Counter
from datetime import datetime

_WHITESPACE_RE = re.compile(r"\s+")
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d")
_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")
_BOOL_TRUE = {"true", "1", "yes", "y", "t"}
_BOOL_FALSE = {"false", "0", "no", "n", "f"}

# Heuristic only -- flags *possible* PII columns by name so a human can
# decide, never auto-redacts or ships anything anywhere.
_PII_NAME_HINTS = (
    "ssn", "social_security", "email", "phone", "dob", "birth", "passport",
    "license", "credit_card", "card_number", "address", "iban", "tax_id",
)


def _is_blank(v):
    return v is None or (isinstance(v, str) and v.strip() == "")


def _looks_numeric(v):
    return isinstance(v, str) and bool(_NUMERIC_RE.match(v.strip()))


def _looks_date(v):
    if not isinstance(v, str) or not v.strip():
        return False
    for fmt in _DATE_FORMATS:
        try:
            datetime.strptime(v.strip(), fmt)
            return True
        except ValueError:
            continue
    return False


def _looks_bool(v):
    if not isinstance(v, str):
        return False
    return v.strip().lower() in _BOOL_TRUE | _BOOL_FALSE


def _quantile(sorted_vals, q):
    if not sorted_vals:
        return None
    idx = q * (len(sorted_vals) - 1)
    lo, hi = math.floor(idx), math.ceil(idx)
    if lo == hi:
        return sorted_vals[lo]
    frac = idx - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def profile_column(values, column_name=""):
    """values: list of raw cell values (already parsed, so usually str/None)."""
    total = len(values)
    blanks = sum(1 for v in values if _is_blank(v))
    empty_strings = sum(1 for v in values if isinstance(v, str) and v == "")
    whitespace_only = sum(1 for v in values if isinstance(v, str) and v != "" and v.strip() == "")
    non_blank = [v for v in values if not _is_blank(v)]
    distinct = len(set(non_blank))
    duplicate_count = max(0, len(non_blank) - distinct)

    numeric_vals = [float(v) for v in non_blank if _looks_numeric(v)]
    invalid_numeric = 0  # only counted when caller asserts this column IS numeric; see note below
    date_like = sum(1 for v in non_blank if _looks_date(v))
    bool_like = sum(1 for v in non_blank if _looks_bool(v))

    lengths = [len(v) for v in non_blank if isinstance(v, str)]
    freq = Counter(non_blank)
    most_common = freq.most_common(5)
    rarest = sorted(freq.items(), key=lambda kv: kv[1])[:5] if freq else []

    stats = {
        "column": column_name,
        "row_count": total,
        "null_count": blanks,
        "null_pct": round(100.0 * blanks / total, 2) if total else 0.0,
        "empty_string_count": empty_strings,
        "whitespace_only_count": whitespace_only,
        "distinct_count": distinct,
        "uniqueness_pct": round(100.0 * distinct / len(non_blank), 2) if non_blank else 0.0,
        "duplicate_count": duplicate_count,
        "min_length": min(lengths) if lengths else None,
        "max_length": max(lengths) if lengths else None,
        "avg_length": round(sum(lengths) / len(lengths), 2) if lengths else None,
        "looks_numeric_count": len(numeric_vals),
        "looks_date_count": date_like,
        "looks_boolean_count": bool_like,
        "most_common_values": most_common,
        "rarest_values": rarest,
        "suspected_pii": any(h in column_name.lower() for h in _PII_NAME_HINTS),
        "warnings": [],
    }

    if numeric_vals:
        s = sorted(numeric_vals)
        stats["numeric_min"] = s[0]
        stats["numeric_max"] = s[-1]
        stats["numeric_mean"] = round(sum(s) / len(s), 4)
        stats["numeric_median"] = _quantile(s, 0.5)
        stats["numeric_p25"] = _quantile(s, 0.25)
        stats["numeric_p75"] = _quantile(s, 0.75)

    # Warnings (data-quality signals, not hard failures)
    if total and blanks / total > 0.5:
        stats["warnings"].append("more than half the values are null or blank")
    if non_blank and distinct == 1:
        stats["warnings"].append("column is constant (only one distinct value)")
    if non_blank and duplicate_count > 0 and distinct / len(non_blank) < 0.5:
        stats["warnings"].append("high duplication: fewer than half the non-null values are unique")
    if whitespace_only:
        stats["warnings"].append(f"{whitespace_only} whitespace-only value(s)")
    if lengths and (max(lengths) - min(lengths)) > 40:
        stats["warnings"].append("wide string-length variance; possible inconsistent formatting")

    return stats


def profile_dataset(columns, rows):
    """rows: list of dicts, as produced by app.parsers.parse_file()."""
    col_profiles = []
    for col in columns:
        values = [r.get(col) for r in rows]
        col_profiles.append(profile_column(values, col))

    # Whole-row duplicate detection (identical across every column).
    row_signatures = Counter(tuple(r.get(c) for c in columns) for r in rows)
    duplicate_rows = sum(count - 1 for count in row_signatures.values() if count > 1)

    return {
        "row_count": len(rows),
        "column_count": len(columns),
        "duplicate_row_count": duplicate_rows,
        "columns": col_profiles,
    }


def identifier_warnings(profile, id_column):
    """Specific checks for a column being used as a matching/record identifier."""
    col = next((c for c in profile["columns"] if c["column"] == id_column), None)
    if not col:
        return [f"identifier column '{id_column}' not found in profile"]
    warnings = []
    if col["null_count"] > 0:
        warnings.append(f"identifier '{id_column}' has {col['null_count']} null value(s)")
    if col["duplicate_count"] > 0:
        warnings.append(f"identifier '{id_column}' has {col['duplicate_count']} duplicate value(s); it is not unique")
    return warnings
