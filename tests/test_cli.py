import json

import pytest
from typer.testing import CliRunner

from tapedeck.cli import app

runner = CliRunner()


@pytest.fixture
def har_file(tmp_path, sample_har):
    p = tmp_path / "session.har"
    p.write_text(json.dumps(sample_har), encoding="utf-8")
    return p


def run(db, *args):
    return runner.invoke(app, [*args, "--db", str(db)])


def test_import_list_show_delete_cycle(tmp_path, har_file):
    db = tmp_path / "t.db"
    out = run(db, "import-har", str(har_file), "--name", "demo")
    assert out.exit_code == 0 and "Imported 4 requests" in out.output and "secrets hidden" in out.output

    listing = run(db, "tapes").output
    assert "demo" in listing and "4" in listing

    shown = run(db, "show", "demo").output
    assert "/login" in shown and "/items/1" in shown and "POST" in shown

    assert run(db, "delete", "demo", "--yes").exit_code == 0
    assert "No tapes yet" in run(db, "tapes").output


def test_tape_name_defaults_to_the_file_name(tmp_path, har_file):
    db = tmp_path / "t.db"
    run(db, "import-har", str(har_file))
    assert "session" in run(db, "tapes").output


def test_importing_the_same_name_twice_is_an_error(tmp_path, har_file):
    db = tmp_path / "t.db"
    assert run(db, "import-har", str(har_file), "--name", "x").exit_code == 0
    again = run(db, "import-har", str(har_file), "--name", "x")
    assert again.exit_code == 1 and "already exists" in again.output


def test_unknown_tape_gives_a_friendly_error(tmp_path):
    out = run(tmp_path / "t.db", "show", "ghost")
    assert out.exit_code == 1 and "No tape called 'ghost'" in out.output


def test_not_a_har_file(tmp_path):
    bad = tmp_path / "bad.har"
    bad.write_text("{}", encoding="utf-8")
    out = run(tmp_path / "t.db", "import-har", str(bad))
    assert out.exit_code == 1 and "HAR" in out.output


def test_version():
    assert "tapedeck 0.1.0" in runner.invoke(app, ["--version"]).output
