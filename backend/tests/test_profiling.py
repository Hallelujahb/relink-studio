from app import profiling

ROWS = [
    {"id": "1", "name": "Alpha", "score": "10"},
    {"id": "2", "name": "Beta", "score": "20"},
    {"id": "2", "name": "  ", "score": "abc"},
    {"id": None, "name": "Alpha", "score": ""},
]
COLUMNS = ["id", "name", "score"]


def test_profile_dataset_shape():
    result = profiling.profile_dataset(COLUMNS, ROWS)
    assert result["row_count"] == 4
    assert result["column_count"] == 3
    assert len(result["columns"]) == 3


def test_profile_column_detects_duplicates_and_nulls():
    id_profile = next(c for c in profiling.profile_dataset(COLUMNS, ROWS)["columns"] if c["column"] == "id")
    assert id_profile["null_count"] == 1
    assert id_profile["duplicate_count"] == 1  # "2" appears twice


def test_profile_column_flags_whitespace_only():
    name_profile = next(c for c in profiling.profile_dataset(COLUMNS, ROWS)["columns"] if c["column"] == "name")
    assert name_profile["whitespace_only_count"] == 1
    assert any("whitespace-only" in w for w in name_profile["warnings"])


def test_profile_column_numeric_stats():
    score_profile = next(c for c in profiling.profile_dataset(COLUMNS, ROWS)["columns"] if c["column"] == "score")
    assert score_profile["looks_numeric_count"] == 2
    assert score_profile["numeric_min"] == 10
    assert score_profile["numeric_max"] == 20


def test_identifier_warnings_flags_nulls_and_duplicates():
    result = profiling.profile_dataset(COLUMNS, ROWS)
    warnings = profiling.identifier_warnings(result, "id")
    assert any("null" in w for w in warnings)
    assert any("duplicate" in w for w in warnings)


def test_identifier_warnings_unknown_column():
    result = profiling.profile_dataset(COLUMNS, ROWS)
    warnings = profiling.identifier_warnings(result, "does_not_exist")
    assert warnings and "not found" in warnings[0]


def test_constant_column_warns():
    rows = [{"x": "same"}, {"x": "same"}, {"x": "same"}]
    result = profiling.profile_dataset(["x"], rows)
    assert any("constant" in w for w in result["columns"][0]["warnings"])


def test_suspected_pii_by_column_name():
    rows = [{"email": "a@example.com"}]
    result = profiling.profile_dataset(["email"], rows)
    assert result["columns"][0]["suspected_pii"] is True


def test_duplicate_row_count():
    rows = [{"a": "1", "b": "2"}, {"a": "1", "b": "2"}, {"a": "3", "b": "4"}]
    result = profiling.profile_dataset(["a", "b"], rows)
    assert result["duplicate_row_count"] == 1
