from app import blocking_diagnostics as bd

CFG = {"strip_parentheticals": True, "strip_suffix_words": True, "keep_digits": True, "case_sensitive": False}

SOURCE = [
    {"id": "1", "name": "Springfield Township"},
    {"id": "2", "name": "Riverside County"},
    {"id": "3", "name": "Zzz Nomatch"},
]
TARGET = [
    {"tid": "101", "tname": "Springfield Twp"},
    {"tid": "102", "tname": "Riverside Co"},
]


def test_analyze_blocking_basic_shape():
    result = bd.analyze_blocking(SOURCE, TARGET, "id", "name", "tid", "tname", CFG)
    assert "total_target_blocks" in result
    assert result["total_target_blocks"] == 2


def test_analyze_blocking_flags_excluded_source_rows():
    result = bd.analyze_blocking(SOURCE, TARGET, "id", "name", "tid", "tname", CFG)
    assert result["source_rows_excluded_by_blocking"] == 1  # "Zzz Nomatch" shares no key


def test_analyze_blocking_reduction_factor_positive():
    result = bd.analyze_blocking(SOURCE, TARGET, "id", "name", "tid", "tname", CFG)
    assert result["reduction_factor"] > 0


def test_analyze_blocking_warns_on_low_coverage():
    source = [{"id": str(i), "name": f"Nomatch{i}"} for i in range(10)]
    result = bd.analyze_blocking(source, TARGET, "id", "name", "tid", "tname", CFG)
    assert any("share no blocking key" in w for w in result["warnings"])


def test_analyze_blocking_empty_key_warning():
    source = [{"id": "1", "name": ""}]
    result = bd.analyze_blocking(source, TARGET, "id", "name", "tid", "tname", CFG)
    assert result["source_rows_with_empty_blocking_key"] == 1
