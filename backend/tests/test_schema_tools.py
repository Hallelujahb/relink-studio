from app import schema_tools


def test_discover_schema_infers_types_and_roles():
    columns = ["id", "email", "amount"]
    rows = [
        {"id": "1", "email": "a@example.com", "amount": "10.5"},
        {"id": "2", "email": "b@example.com", "amount": "20.0"},
    ]
    schema = schema_tools.discover_schema(columns, rows)
    by_name = {f["name"]: f for f in schema["fields"]}
    assert by_name["id"]["is_likely_identifier"] is True
    assert by_name["email"]["semantic_role"] == "email"
    assert by_name["amount"]["dtype"] == "float"


def test_discover_schema_geometry():
    schema = schema_tools.discover_schema(["id"], [{"id": "1"}], geometry=[{"type": "Point"}])
    assert schema["has_geometry"] is True
    assert schema["geometry_type"] == "Point"


def test_diff_schema_detects_added_and_removed_fields():
    old = schema_tools.discover_schema(["id", "name"], [{"id": "1", "name": "A"}])
    new = schema_tools.discover_schema(["id", "email"], [{"id": "1", "email": "a@x.com"}])
    diff = schema_tools.diff_schema(old, new)
    assert diff["has_drift"] is True
    assert diff["added_fields"] == ["email"]
    assert diff["removed_fields"] == ["name"]


def test_diff_schema_detects_type_change():
    old = schema_tools.discover_schema(["amount"], [{"amount": "abc"}])
    new = schema_tools.discover_schema(["amount"], [{"amount": "10"}])
    diff = schema_tools.diff_schema(old, new)
    assert diff["has_drift"] is True
    assert diff["type_changes"][0]["field"] == "amount"


def test_diff_schema_no_drift_when_identical():
    schema = schema_tools.discover_schema(["id", "name"], [{"id": "1", "name": "A"}])
    diff = schema_tools.diff_schema(schema, schema)
    assert diff["has_drift"] is False
    assert diff["added_fields"] == []
    assert diff["removed_fields"] == []


def test_diff_schema_detects_geometry_type_change():
    old = schema_tools.discover_schema(["id"], [{"id": "1"}], geometry=[{"type": "Point"}])
    new = schema_tools.discover_schema(["id"], [{"id": "1"}], geometry=[{"type": "Polygon"}])
    diff = schema_tools.diff_schema(old, new)
    assert diff["geometry_type_changed"] is True
