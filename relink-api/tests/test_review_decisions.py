import pytest

from app import review_decisions as rd


def test_record_valid_decision():
    log = rd.DecisionLog()
    d = rd.ReviewDecision(decision_type="approve", reviewer="carol", source_id="1",
                           target_id="t1", confidence_at_decision=0.95, config_version=1)
    log.record(d)
    assert log.current("1").decision_type == "approve"


def test_invalid_decision_type_raises():
    log = rd.DecisionLog()
    d = rd.ReviewDecision(decision_type="maybe", reviewer="carol", source_id="1",
                           target_id=None, confidence_at_decision=None, config_version=1)
    with pytest.raises(ValueError):
        log.record(d)


def test_reject_with_invalid_reason_raises():
    log = rd.DecisionLog()
    d = rd.ReviewDecision(decision_type="reject", reviewer="carol", source_id="1",
                           target_id=None, confidence_at_decision=None, config_version=1,
                           reason="not_a_real_reason")
    with pytest.raises(ValueError):
        log.record(d)


def test_history_accumulates():
    log = rd.DecisionLog()
    log.record(rd.ReviewDecision(decision_type="approve", reviewer="carol", source_id="1",
                                  target_id="t1", confidence_at_decision=0.9, config_version=1))
    log.record(rd.ReviewDecision(decision_type="reject", reviewer="dave", source_id="1",
                                  target_id="t1", confidence_at_decision=0.9, config_version=1,
                                  reason="wrong_entity"))
    history = log.history_for("1")
    assert len(history) == 2
    assert history[-1].reviewer == "dave"
    assert history[-1].previous_decision_id == "1#0"


def test_undo_restores_previous_decision():
    log = rd.DecisionLog()
    log.record(rd.ReviewDecision(decision_type="approve", reviewer="carol", source_id="1",
                                  target_id="t1", confidence_at_decision=0.9, config_version=1))
    log.record(rd.ReviewDecision(decision_type="reject", reviewer="dave", source_id="1",
                                  target_id="t1", confidence_at_decision=0.9, config_version=1,
                                  reason="wrong_entity"))
    restored = log.undo("1")
    assert restored.decision_type == "approve"
    assert log.current("1").decision_type == "approve"


def test_undo_with_no_prior_state_returns_none():
    log = rd.DecisionLog()
    log.record(rd.ReviewDecision(decision_type="approve", reviewer="carol", source_id="1",
                                  target_id="t1", confidence_at_decision=0.9, config_version=1))
    assert log.undo("1") is None
