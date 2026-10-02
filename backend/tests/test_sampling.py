from app import sampling as sm

ROWS = [
    {"sourceId": "1", "kind": "consensus", "methods": {"A": {"score": 0.95}}},
    {"sourceId": "2", "kind": "low_confidence", "methods": {"A": {"score": 0.5}}},
    {"sourceId": "3", "kind": "unmatched", "methods": {}},
    {"sourceId": "4", "kind": "collision", "methods": {"A": {"score": 0.9}, "B": {"score": 0.6}}},
]


def test_random_sample_is_reproducible_with_seed():
    a = sm.random_sample(ROWS, 2, seed=42)
    b = sm.random_sample(ROWS, 2, seed=42)
    assert [r["sourceId"] for r in a] == [r["sourceId"] for r in b]


def test_deterministic_sample_is_order_independent_of_input_order():
    shuffled = list(reversed(ROWS))
    a = sm.deterministic_sample(ROWS, 2)
    b = sm.deterministic_sample(shuffled, 2)
    assert [r["sourceId"] for r in a] == [r["sourceId"] for r in b]


def test_lowest_confidence_sample_orders_ascending():
    result = sm.lowest_confidence_sample(ROWS, 2)
    assert result[0]["sourceId"] == "2"  # score 0.5 is lowest


def test_highest_confidence_sample_orders_descending():
    result = sm.highest_confidence_sample(ROWS, 1)
    assert result[0]["sourceId"] == "1"  # score 0.95 is highest


def test_largest_score_gap_sample_picks_biggest_disagreement():
    result = sm.largest_score_gap_sample(ROWS, 1)
    assert result[0]["sourceId"] == "4"  # only row with 2 scored methods (0.9 vs 0.6)


def test_by_kind_sample_filters_correctly():
    result = sm.by_kind_sample(ROWS, "unmatched")
    assert len(result) == 1
    assert result[0]["sourceId"] == "3"


def test_by_kind_sample_respects_cap():
    rows = ROWS + [{"sourceId": "5", "kind": "unmatched", "methods": {}}]
    result = sm.by_kind_sample(rows, "unmatched", n=1)
    assert len(result) == 1
