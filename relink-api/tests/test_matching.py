from app import matching


def test_normalize_strips_parentheticals_and_suffix_words():
    cfg = {"strip_parentheticals": True, "strip_suffix_words": True, "keep_digits": True, "case_sensitive": False}
    assert matching.normalize("Springfield Township (Old)", cfg) == "springfield"


def test_normalize_case_sensitive_preserves_case():
    cfg = {"case_sensitive": True, "strip_parentheticals": False, "strip_suffix_words": False, "keep_digits": True}
    assert matching.normalize("Oak Grove", cfg) == "Oak Grove"


def test_normalize_keep_digits_false_removes_numbers():
    cfg = {"keep_digits": False, "strip_parentheticals": False, "strip_suffix_words": False, "case_sensitive": False}
    assert "5" not in matching.normalize("Route 5 Township", cfg)


def _cfg(**overrides):
    base = {"strip_parentheticals": True, "strip_suffix_words": True, "keep_digits": True, "case_sensitive": False}
    base.update(overrides)
    return base


SOURCE = [
    {"id": "1", "name": "Springfield Township"},
    {"id": "2", "name": "Riverside County"},
]
TARGET = [
    {"tid": "101", "tname": "Springfield Twp"},
    {"tid": "102", "tname": "Riverside Co"},
]


def test_method_a_fuzzy_finds_best_match():
    results = matching.method_a_fuzzy(SOURCE, TARGET, "id", "name", "tid", "tname", _cfg(), 0.3)
    assert results["1"]["targetId"] == "101"
    assert results["2"]["targetId"] == "102"
    assert 0 < results["1"]["score"] <= 1


def test_method_a_fuzzy_respects_blocking_floor():
    # An impossibly high floor should return no match, not a low-confidence one.
    results = matching.method_a_fuzzy(SOURCE, TARGET, "id", "name", "tid", "tname", _cfg(), 0.999)
    assert results["1"]["targetId"] is None


def test_method_b_token_overlap_finds_best_match():
    results = matching.method_b_token_overlap(SOURCE, TARGET, "id", "name", "tid", "tname", _cfg(), 0.2)
    assert results["1"]["targetId"] == "101"


def test_method_d_geometry_flags_distant_candidate():
    source_geom = [{"type": "Point", "coordinates": [0.0, 0.0]}]
    target_geom = [{"type": "Point", "coordinates": [10.0, 10.0]}]  # ~1500km away
    flags = matching.method_d_geometry_corroboration(source_geom, target_geom, {0: 0}, max_km=25)
    assert flags[0]["suspect"] is True
    assert flags[0]["distance_km"] > 25


def test_method_d_geometry_ok_when_close():
    source_geom = [{"type": "Point", "coordinates": [0.0, 0.0]}]
    target_geom = [{"type": "Point", "coordinates": [0.001, 0.001]}]  # ~150m away
    flags = matching.method_d_geometry_corroboration(source_geom, target_geom, {0: 0}, max_km=25)
    assert flags[0]["suspect"] is False


def test_combine_methods_auto_approves_above_threshold():
    method_results = {"A": {"1": {"name": "Springfield Twp", "targetId": "101", "score": 0.95}}}
    config = {"thresholds": {"auto_approve": 0.9, "needs_review": 0.5}, "safety": {}}
    rows = matching.combine_methods(
        [{"id": "1", "name": "Springfield Township"}], "id", "name", method_results,
        TARGET, "tid", None, None, config)
    assert rows[0]["approved"] is True
    assert rows[0]["kind"] == "consensus"


def test_combine_methods_low_confidence_not_approved():
    method_results = {"A": {"1": {"name": "Springfield Twp", "targetId": "101", "score": 0.3}}}
    config = {"thresholds": {"auto_approve": 0.9, "needs_review": 0.5}, "safety": {}}
    rows = matching.combine_methods(
        [{"id": "1", "name": "Springfield Township"}], "id", "name", method_results,
        TARGET, "tid", None, None, config)
    assert rows[0]["approved"] is False
    assert rows[0]["kind"] == "low_confidence"


def test_combine_methods_flags_type_leak():
    method_results = {"A": {"1": {"name": "Riverside Co", "targetId": "102", "score": 0.95}}}
    target = [{"tid": "102", "tname": "Riverside Co", "ttype": "county"}]
    config = {"thresholds": {"auto_approve": 0.9, "needs_review": 0.5}, "safety": {}}
    rows = matching.combine_methods(
        [{"id": "1", "name": "Riverside Township"}], "id", "name", method_results,
        target, "tid", "ttype", "municipal", config)
    assert rows[0]["kind"] == "type_leak"
    assert rows[0]["approved"] is False


def test_combine_methods_collision_guard_flags_duplicate_claims():
    method_results = {
        "A": {
            "1": {"name": "Springfield Twp", "targetId": "101", "score": 0.95},
            "2": {"name": "Springfield Twp", "targetId": "101", "score": 0.93},
        }
    }
    config = {"thresholds": {"auto_approve": 0.9, "needs_review": 0.5}, "safety": {"collision_guard": True}}
    rows = matching.combine_methods(
        [{"id": "1", "name": "A"}, {"id": "2", "name": "B"}], "id", "name", method_results,
        TARGET, "tid", None, None, config)
    kinds = {r["sourceId"]: r["kind"] for r in rows}
    assert kinds["1"] == "collision"
    assert kinds["2"] == "collision"
    assert all(not r["approved"] for r in rows)


def test_combine_methods_auto_downgrade_ties():
    method_results = {
        "A": {"1": {"name": "X", "targetId": "101", "score": 0.95}},
        "B": {"1": {"name": "Y", "targetId": "102", "score": 0.95}},
    }
    config = {"thresholds": {"auto_approve": 0.9, "needs_review": 0.5}, "safety": {"auto_downgrade_ties": True}}
    rows = matching.combine_methods(
        [{"id": "1", "name": "A"}], "id", "name", method_results, TARGET, "tid", None, None, config)
    assert rows[0]["approved"] is False


def test_combine_methods_unmatched_when_no_candidates():
    config = {"thresholds": {"auto_approve": 0.9, "needs_review": 0.5}, "safety": {}}
    rows = matching.combine_methods(
        [{"id": "1", "name": "Nowhere"}], "id", "name", {}, TARGET, "tid", None, None, config)
    assert rows[0]["kind"] == "unmatched"
    assert rows[0]["approved"] is False
