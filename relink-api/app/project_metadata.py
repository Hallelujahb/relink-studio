"""
Project metadata (section 1 of the wishlist) and project profiles.

Pure data + validation, no I/O. Whatever persists this (a projects table
column, a JSON file) is a separate concern -- this module defines the
shape and the defaults-by-profile behavior only.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

PROJECT_TYPES = (
    "general_entity_resolution", "customer_deduplication",
    "supplier_vendor_reconciliation", "organization_matching",
    "facility_site_matching", "address_matching",
    "inventory_product_reconciliation", "gis_geometry_reconciliation",
    "transaction_to_master_data", "custom_data_quality_workflow",
)

ENVIRONMENTS = ("development", "test", "production")
STATUSES = ("draft", "active", "paused", "archived")

# Profile defaults only *suggest* config; the user can override every value.
# Keys mirror ProjectConfig.thresholds / matching / safety in matching.py.
_PROFILE_DEFAULTS = {
    "general_entity_resolution": {"auto_approve": 0.95, "needs_review": 0.80, "methods": ["fuzzy", "recordlinkage"]},
    "customer_deduplication": {"auto_approve": 0.93, "needs_review": 0.75, "methods": ["fuzzy", "recordlinkage"]},
    "supplier_vendor_reconciliation": {"auto_approve": 0.90, "needs_review": 0.70, "methods": ["fuzzy", "recordlinkage"]},
    "organization_matching": {"auto_approve": 0.92, "needs_review": 0.75, "methods": ["fuzzy", "recordlinkage"]},
    "facility_site_matching": {"auto_approve": 0.95, "needs_review": 0.80, "methods": ["fuzzy", "recordlinkage", "geometry_corroboration"]},
    "address_matching": {"auto_approve": 0.90, "needs_review": 0.70, "methods": ["fuzzy"]},
    "inventory_product_reconciliation": {"auto_approve": 0.93, "needs_review": 0.78, "methods": ["fuzzy", "recordlinkage"]},
    "gis_geometry_reconciliation": {"auto_approve": 0.90, "needs_review": 0.70, "methods": ["geometry_corroboration", "fuzzy"]},
    "transaction_to_master_data": {"auto_approve": 0.97, "needs_review": 0.85, "methods": ["fuzzy", "recordlinkage"]},
    "custom_data_quality_workflow": {"auto_approve": 0.95, "needs_review": 0.80, "methods": ["fuzzy"]},
}


def _now():
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ProjectMetadata:
    name: str
    project_type: str = "general_entity_resolution"
    description: Optional[str] = None
    owner: Optional[str] = None
    business_purpose: Optional[str] = None
    source_systems: list = field(default_factory=list)
    target_system: Optional[str] = None
    data_domain: Optional[str] = None
    environment: str = "development"
    status: str = "draft"
    is_archived: bool = False
    tags: list = field(default_factory=list)
    notes: Optional[str] = None
    schema_version: int = 1
    config_version: int = 1
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    last_successful_run_at: Optional[str] = None
    last_failed_run_at: Optional[str] = None
    last_export_at: Optional[str] = None

    def validate(self):
        errors = []
        if not self.name or not self.name.strip():
            errors.append("name is required")
        if self.project_type not in PROJECT_TYPES:
            errors.append(f"project_type must be one of {PROJECT_TYPES}")
        if self.environment not in ENVIRONMENTS:
            errors.append(f"environment must be one of {ENVIRONMENTS}")
        if self.status not in STATUSES:
            errors.append(f"status must be one of {STATUSES}")
        return errors

    def touch(self):
        self.updated_at = _now()

    def mark_run(self, success: bool):
        self.touch()
        if success:
            self.last_successful_run_at = self.updated_at
        else:
            self.last_failed_run_at = self.updated_at

    def mark_export(self):
        self.touch()
        self.last_export_at = self.updated_at

    def data_freshness(self):
        """How stale the last successful run is, relative to now. Returns
        None when there has never been a successful run."""
        if not self.last_successful_run_at:
            return None
        then = datetime.fromisoformat(self.last_successful_run_at)
        return (datetime.now(timezone.utc) - then).total_seconds()

    def to_dict(self):
        return asdict(self)


def default_config_for_profile(project_type: str) -> dict:
    if project_type not in PROJECT_TYPES:
        raise ValueError(f"Unknown project_type: {project_type}")
    return dict(_PROFILE_DEFAULTS[project_type])
