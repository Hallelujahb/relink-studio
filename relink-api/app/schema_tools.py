"""
Schema discovery and drift detection.

A "schema snapshot" is a small, serializable dict captured at import time,
so a later re-import of the "same" source can be diffed against it. This
is what lets ReLink warn about drift on repeated/incremental imports
instead of silently matching against a changed shape.
"""
import re

_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")

SEMANTIC_ROLES = (
    "primary_identifier", "foreign_identifier", "name", "legal_name",
    "address", "email", "phone", "date", "numeric_measure",
    "categorical_attribute", "geographic_coordinate", "geometry",
    "free_text", "metadata", "ignored",
)

_ROLE_NAME_HINTS = {
    "email": ("email",),
    "phone": ("phone", "tel"),
    "date": ("date", "_at", "timestamp", "dob"),
    "primary_identifier": ("id", "_id", "uid", "uuid"),
    "geographic_coordinate": ("lat", "lon", "lng", "latitude", "longitude"),
    "address": ("address", "addr", "street"),
}


def _infer_dtype(values):
    non_null = [v for v in values if v is not None and v != ""]
    if not non_null:
        return "unknown"
    if all(_NUMERIC_RE.match(str(v)) for v in non_null):
        return "float" if any("." in str(v) for v in non_null) else "integer"
    return "string"


def _infer_role(column_name, dtype):
    lower = column_name.lower()
    for role, hints in _ROLE_NAME_HINTS.items():
        if any(h in lower for h in hints):
            return role
    if dtype in ("integer", "float"):
        return "numeric_measure"
    return "free_text"


def discover_schema(columns, rows, geometry=None):
    """Build a schema snapshot from parsed rows (see app.parsers.parse_file)."""
    fields = []
    for col in columns:
        values = [r.get(col) for r in rows]
        non_null = sum(1 for v in values if v not in (None, ""))
        distinct = len(set(v for v in values if v not in (None, "")))
        dtype = _infer_dtype(values)
        fields.append({
            "name": col,
            "dtype": dtype,
            "nullable": non_null < len(values),
            "distinct_count": distinct,
            "is_likely_identifier": distinct == non_null and non_null > 0 and non_null == len(values),
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
    """Compare two schema snapshots from discover_schema(). Returns a
    structured diff; never mutates or silently accepts either input."""
    old_fields = {f["name"]: f for f in old_schema.get("fields", [])}
    new_fields = {f["name"]: f for f in new_schema.get("fields", [])}

    added = sorted(set(new_fields) - set(old_fields))
    removed = sorted(set(old_fields) - set(new_fields))
    common = set(old_fields) & set(new_fields)

    type_changes, nullability_changes, identifier_changes, role_changes = [], [], [], []
    for name in sorted(common):
        o, n = old_fields[name], new_fields[name]
        if o["dtype"] != n["dtype"]:
            type_changes.append({"field": name, "old": o["dtype"], "new": n["dtype"]})
        if o["nullable"] != n["nullable"]:
            nullability_changes.append({"field": name, "old": o["nullable"], "new": n["nullable"]})
        if o["is_likely_identifier"] != n["is_likely_identifier"]:
            identifier_changes.append({"field": name, "old": o["is_likely_identifier"], "new": n["is_likely_identifier"]})
        if o["semantic_role"] != n["semantic_role"]:
            role_changes.append({"field": name, "old": o["semantic_role"], "new": n["semantic_role"]})

    geometry_changed = old_schema.get("geometry_type") != new_schema.get("geometry_type")

    has_drift = bool(added or removed or type_changes or nullability_changes or identifier_changes or geometry_changed)

    return {
        "has_drift": has_drift,
        "added_fields": added,
        "removed_fields": removed,
        "type_changes": type_changes,
        "nullability_changes": nullability_changes,
        "identifier_changes": identifier_changes,
        "semantic_role_changes": role_changes,
        "geometry_type_changed": geometry_changed,
        "old_geometry_type": old_schema.get("geometry_type"),
        "new_geometry_type": new_schema.get("geometry_type"),
    }
