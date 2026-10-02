import json

from app import cli_tools


def test_profile_command_on_valid_csv(tmp_path, capsys):
    f = tmp_path / "data.csv"
    f.write_text("id,name\n1,Alpha\n2,Beta\n")
    exit_code = cli_tools.main(["profile", str(f)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "2 rows" in out


def test_profile_command_json_output(tmp_path, capsys):
    f = tmp_path / "data.csv"
    f.write_text("id,name\n1,Alpha\n")
    exit_code = cli_tools.main(["profile", str(f), "--json"])
    out = capsys.readouterr().out
    assert exit_code == 0
    parsed = json.loads(out)
    assert parsed["row_count"] == 1


def test_profile_command_on_missing_file_returns_error(capsys):
    exit_code = cli_tools.main(["profile", "/nonexistent/file/path.csv"])
    assert exit_code == 1  # ParseError from parse_file, handled as a normal user-facing error
    assert "error" in capsys.readouterr().err


def test_validate_config_valid(tmp_path, capsys):
    f = tmp_path / "config.json"
    f.write_text(json.dumps({"project_name": "x", "source": {}, "target": {}, "methods": ["fuzzy"]}))
    exit_code = cli_tools.main(["validate-config", str(f)])
    assert exit_code == 0
    assert "valid" in capsys.readouterr().out


def test_validate_config_missing_keys(tmp_path, capsys):
    f = tmp_path / "config.json"
    f.write_text(json.dumps({"project_name": "x"}))
    exit_code = cli_tools.main(["validate-config", str(f)])
    assert exit_code == 1
    assert "missing" in capsys.readouterr().err


def test_validate_config_bad_json(tmp_path, capsys):
    f = tmp_path / "config.json"
    f.write_text("{not valid json")
    exit_code = cli_tools.main(["validate-config", str(f)])
    assert exit_code == 1


def test_schema_diff_detects_drift(tmp_path, capsys):
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    old.write_text(json.dumps({"columns": ["id", "name"], "rows": [{"id": "1", "name": "A"}]}))
    new.write_text(json.dumps({"columns": ["id", "email"], "rows": [{"id": "1", "email": "a@x.com"}]}))
    exit_code = cli_tools.main(["schema-diff", str(old), str(new)])
    assert exit_code == 1
    assert "DRIFT DETECTED" in capsys.readouterr().out


def test_schema_diff_no_drift(tmp_path, capsys):
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    payload = json.dumps({"columns": ["id"], "rows": [{"id": "1"}]})
    old.write_text(payload)
    new.write_text(payload)
    exit_code = cli_tools.main(["schema-diff", str(old), str(new)])
    assert exit_code == 0
    assert "no drift" in capsys.readouterr().out


def test_check_capabilities_json(capsys):
    exit_code = cli_tools.main(["check-capabilities", "--json"])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert exit_code == 0
    assert "postgresql" in parsed and "clickhouse" in parsed


def test_check_capabilities_human_readable(capsys):
    exit_code = cli_tools.main(["check-capabilities"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "postgresql" in out
