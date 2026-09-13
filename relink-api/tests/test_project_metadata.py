import pytest

from app import project_metadata as pm


def test_default_project_is_valid():
    p = pm.ProjectMetadata(name="Facility Recon")
    assert p.validate() == []


def test_missing_name_is_invalid():
    p = pm.ProjectMetadata(name="  ")
    assert "name is required" in p.validate()


def test_invalid_project_type_rejected():
    p = pm.ProjectMetadata(name="X", project_type="not_a_real_type")
    assert any("project_type" in e for e in p.validate())


def test_mark_run_updates_timestamps():
    p = pm.ProjectMetadata(name="X")
    assert p.last_successful_run_at is None
    p.mark_run(success=True)
    assert p.last_successful_run_at is not None
    assert p.last_failed_run_at is None


def test_mark_run_failure_sets_failed_timestamp():
    p = pm.ProjectMetadata(name="X")
    p.mark_run(success=False)
    assert p.last_failed_run_at is not None
    assert p.last_successful_run_at is None


def test_data_freshness_none_before_first_run():
    p = pm.ProjectMetadata(name="X")
    assert p.data_freshness() is None


def test_data_freshness_after_run_is_small_positive_number():
    p = pm.ProjectMetadata(name="X")
    p.mark_run(success=True)
    assert p.data_freshness() >= 0


def test_default_config_for_profile_returns_expected_keys():
    cfg = pm.default_config_for_profile("gis_geometry_reconciliation")
    assert "methods" in cfg and "geometry_corroboration" in cfg["methods"]


def test_default_config_for_unknown_profile_raises():
    with pytest.raises(ValueError):
        pm.default_config_for_profile("nonsense")


def test_to_dict_roundtrip_shape():
    p = pm.ProjectMetadata(name="X", tags=["gis", "prod"])
    d = p.to_dict()
    assert d["name"] == "X"
    assert d["tags"] == ["gis", "prod"]
