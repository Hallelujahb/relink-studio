"""
Schema discovery and drift detection. A snapshot is a small dict taken at
import time, so a later import of the "same" source can be compared to it.
"""
import re

_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")
_TOKEN_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])")

SEMANTIC_ROLES = (
    "primary_identifier", "foreign_identifier", "name", "legal_name",
    "address", "email", "phone", "date", "numeric_measure",
    "categorical_attribute", "geographic_coordinate", "geometry",
    "free_text", "metadata", "ignored",
)

# Matched against whole words of the column name, so "hotel" is not a phone
# and "width" is not an identifier.
_ROLE_TOKENS = (
    ("email", {"email", "mail"}),
    ("phone", {"phone", "tel", "mobile", "fax"}),
    ("date", {"date", "timestamp", "dob", "at"}),
    ("geographic_coordinate", {"lat", "lon", "lng", "latitude", "longitude"}),
    ("address", {"address", "addr", "street"}),
    ("primary_identifier", {"id", "uid", "uuid"}),
)


def _tokens(column_name):
    return {t.lower() for t in _TOKEN_SPLIT_RE.split(column_name) if t}


def _infer_dtype(values):
    non_null = [v for v in values if v is not None and v != ""]
    if not non_null:
        return "unknown"
    if all(_NUMERIC_RE.match(str(v)) for v in non_null):
        return "float" if any("." in str(v) for v in non_null) else "integer"
    return "string"


def _infer_role(column_name, dtype):
    tokens = _tokens(column_name)
    for role, words in _ROLE_TOKENS:
        if tokens & words:
            return role
    return "numeric_measure" if dtype in ("integer", "float") else "free_text"


def discover_schema(columns, rows, geometry=None):
    fields = []
    for col in columns:
        values = [r.get(col) for r in rows]
        present = [v for v in values if v not in (None, "")]
        distinct = len(set(present))
        dtype = _infer_dtype(values)
        fields.append({
            "name": col,
            "dtype": dtype,
            "nullable": len(present) < len(values),
            "distinct_count": distinct,
            "is_likely_identifier": bool(present) and distinct == len(present) == len(values),
            "semantic_role": _infer_role(col, dtype),
        })
    return {
        "field_count": len(fields),
        "row_count": len(rows),
        "fields": fields,
        "has_geometry": bool(geometry),
        "geometry_type": geometry[0].get("type") if geometry and geometry[0] else None,
    }


def diff_schema(old_schema, new_schema):
    """Structured comparison of two snapshots. Neither input is changed."""
    old_fields = {f["name"]: f for f in old_schema.get("fields", [])}
    new_fields = {f["name"]: f for f in new_schema.get("fields", [])}
    added = sorted(set(new_fields) - set(old_fields))
    removed = sorted(set(old_fields) - set(new_fields))

    def changes(key):
        return [{"field": n, "old": old_fields[n][key], "new": new_fields[n][key]}
                for n in sorted(set(old_fields) & set(new_fields)) if old_fields[n][key] != new_fields[n][key]]

    type_changes, nullability = changes("dtype"), changes("nullable")
    identifier, roles = changes("is_likely_identifier"), changes("semantic_role")
    geometry_changed = old_schema.get("geometry_type") != new_schema.get("geometry_type")

    return {
        "has_drift": bool(added or removed or type_changes or nullability or identifier or roles or geometry_changed),
        "added_fields": added,
        "removed_fields": removed,
        "type_changes": type_changes,
        "nullability_changes": nullability,
        "identifier_changes": identifier,
        "semantic_role_changes": roles,
        "geometry_type_changed": geometry_changed,
        "old_geometry_type": old_schema.get("geometry_type"),
        "new_geometry_type": new_schema.get("geometry_type"),
    }
