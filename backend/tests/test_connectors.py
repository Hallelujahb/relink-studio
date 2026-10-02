import pytest

from app import connectors


def test_redact_connection_config_hides_password():
    cfg = {"host": "db.local", "password": "hunter2", "port": 5432}
    redacted = connectors.redact_connection_config(cfg)
    assert redacted["password"] == "***redacted***"
    assert redacted["host"] == "db.local"


def test_redact_connection_config_handles_empty_password():
    redacted = connectors.redact_connection_config({"password": ""})
    assert redacted["password"] is None


def test_connection_config_rejects_credentials_in_host():
    cfg = connectors.ConnectionConfig(host="user:pass@db.local", port=5432, database="d", password="x")
    errors = cfg.validate()
    assert any("must not be embedded" in e for e in errors)


def test_connection_config_requires_core_fields():
    cfg = connectors.ConnectionConfig(host="", port=0, database="")
    errors = cfg.validate()
    assert len(errors) == 3


def test_list_capabilities_reports_file_always_available():
    caps = connectors.list_capabilities()
    assert caps["file"]["available"] is True
    assert "postgresql" in caps
    assert "clickhouse" in caps


def test_postgres_provider_raises_clear_error_when_driver_missing():
    cap = connectors.detect_postgres_capability()
    cfg = connectors.ConnectionConfig(host="localhost", port=5432, database="d")
    if cap["available"]:
        pytest.skip("psycopg2 is installed in this environment; unavailable-path not exercised")
    with pytest.raises(connectors.ProviderUnavailable):
        connectors.get_provider("postgresql", cfg)


def test_clickhouse_provider_raises_clear_error_when_driver_missing():
    cap = connectors.detect_clickhouse_capability()
    cfg = connectors.ConnectionConfig(host="localhost", port=8123, database="d")
    if cap["available"]:
        pytest.skip("clickhouse_connect is installed in this environment; unavailable-path not exercised")
    with pytest.raises(connectors.ProviderUnavailable):
        connectors.get_provider("clickhouse", cfg)


def test_get_provider_unknown_name_raises_value_error():
    with pytest.raises(ValueError):
        connectors.get_provider("mongodb", None)


def test_is_safe_identifier_accepts_normal_names():
    assert connectors.is_safe_identifier("customer_id")
    assert connectors.is_safe_identifier("Table1")


def test_is_safe_identifier_rejects_injection_attempts():
    assert not connectors.is_safe_identifier("id; DROP TABLE users;--")
    assert not connectors.is_safe_identifier("id\"; --")
    assert not connectors.is_safe_identifier("1id")
    assert not connectors.is_safe_identifier("")
    assert not connectors.is_safe_identifier(None)


def test_quote_identifier_rejects_unsafe_input():
    with pytest.raises(ValueError):
        connectors.quote_identifier("id; DROP TABLE users;--")


def test_quote_identifier_quotes_safe_input():
    assert connectors.quote_identifier("customer_id") == '"customer_id"'


def test_build_select_query_star_and_limit():
    q = connectors.build_select_query("customers", limit=100, offset=200)
    assert q == 'SELECT * FROM "customers" LIMIT 100 OFFSET 200'


def test_build_select_query_with_schema_and_columns():
    q = connectors.build_select_query("customers", columns=["id", "name"], schema="public")
    assert q == 'SELECT "id", "name" FROM "public"."customers"'


def test_build_select_query_rejects_unsafe_table_name():
    with pytest.raises(ValueError):
        connectors.build_select_query("customers; DROP TABLE users;--")
