import pytest
from datetime import datetime, timedelta, timezone

from app import exceptions_mgmt as em


def test_add_valid_exception():
    reg = em.ExceptionRegistry()
    exc = em.Exception_(scope="approved_many_to_one", reason="shared parent office", creator="carol",
                         affected_target_id="t1")
    reg.add(exc)
    assert len(reg.all()) == 1


def test_invalid_scope_raises():
    reg = em.ExceptionRegistry()
    exc = em.Exception_(scope="not_a_scope", reason="x", creator="carol")
    with pytest.raises(ValueError):
        reg.add(exc)


def test_missing_reason_raises():
    reg = em.ExceptionRegistry()
    exc = em.Exception_(scope="ignored_record", reason="", creator="carol")
    with pytest.raises(ValueError):
        reg.add(exc)


def test_expired_exception_not_effective():
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    exc = em.Exception_(scope="ignored_record", reason="x", creator="carol", expires_at=past)
    assert exc.is_expired() is True
    assert exc.is_effective() is False


def test_future_expiry_still_effective():
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    exc = em.Exception_(scope="ignored_record", reason="x", creator="carol", expires_at=future)
    assert exc.is_effective() is True


def test_deactivate_records_history_and_becomes_ineffective():
    exc = em.Exception_(scope="ignored_record", reason="x", creator="carol")
    exc.deactivate(actor="dave", reason="no longer needed")
    assert exc.active is False
    assert exc.is_effective() is False
    assert exc.history[0]["actor"] == "dave"


def test_applies_to_matches_source_and_scope():
    reg = em.ExceptionRegistry()
    reg.add(em.Exception_(scope="allowed_type_mismatch", reason="known exception", creator="carol",
                           affected_source_id="1"))
    found = reg.applies_to("1", "allowed_type_mismatch")
    assert found is not None
    assert reg.applies_to("1", "accepted_geometry_difference") is None
    assert reg.applies_to("2", "allowed_type_mismatch") is None


def test_active_for_source_excludes_inactive():
    reg = em.ExceptionRegistry()
    exc = reg.add(em.Exception_(scope="ignored_record", reason="x", creator="carol", affected_source_id="1"))
    exc.deactivate("carol")
    assert reg.active_for_source("1") == []
