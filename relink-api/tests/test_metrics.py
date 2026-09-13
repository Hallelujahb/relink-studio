from app import metrics as mx

ROWS = [
    {"sourceId": "1", "approved": True, "kind": "consensus", "methods": {"A": {"targetId": "t1", "score": 0.95}}},
    {"sourceId": "2", "approved": False, "kind": "low_confidence", "methods": {"A": {"targetId": "t2", "score": 0.5}}},
    {"sourceId": "3", "approved": False, "kind": "unmatched", "methods": {}},
    {"sourceId": "4", "approved": False, "kind": "collision", "methods": {"A": {"targetId": "t1", "score": 0.9}}},
]


def test_run_metrics_counts():
    result = mx.run_metrics(ROWS, source_count=4, target_count=2)
    assert result["tier"] == "observed"
    assert result["approved_count"] == 1
    assert result["unmatched_count"] == 1
    assert result["collision_count"] == 1


def test_run_metrics_score_stats():
    result = mx.run_metrics(ROWS, source_count=4, target_count=2)
    assert result["average_score"] == round((0.95 + 0.5 + 0.9) / 3, 4)


def test_run_metrics_with_duration():
    result = mx.run_metrics(ROWS, source_count=4, target_count=2, duration_seconds=2.0)
    assert result["records_per_second"] == 2.0


def test_evaluation_metrics_unavailable_without_ground_truth():
    result = mx.evaluation_metrics(ROWS)
    assert result["tier"] == "unavailable"
    assert "ground truth" in result["reason"]


def test_evaluation_metrics_with_ground_truth():
    ground_truth = {"1": "t1", "2": None, "3": None, "4": "t1"}
    result = mx.evaluation_metrics(ROWS, ground_truth)
    assert result["tier"] == "ground_truth"
    assert result["true_positives"] == 1  # row 1 approved+correct
    assert result["precision"] is not None
    assert result["recall"] is not None
