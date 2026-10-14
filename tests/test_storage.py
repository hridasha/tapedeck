import pytest

from tapedeck.core.models import Entry
from tapedeck.core.storage import Storage, TapeExistsError, TapeNotFoundError


@pytest.fixture
def store(tmp_path):
    with Storage(tmp_path / "t.db") as s:
        yield s


def make_entry(i, body=b"hello"):
    return Entry(
        method="GET",
        path=f"/p/{i}",
        query="a=1",
        req_headers={"x": "y"},
        status=200,
        resp_headers={"content-type": "text/plain"},
        resp_body=body,
        duration_ms=1.5,
        order=i,
        started_at="2025-01-01T00:00:00Z",
    )


def test_round_trip_keeps_every_field(store):
    tape = store.create_tape("demo", source="har", target_url="http://x")
    store.add_entries(tape.id, [make_entry(0), make_entry(1)])
    back = store.get_entries(tape.id)
    assert [e.path for e in back] == ["/p/0", "/p/1"]
    e = back[0]
    assert (e.query, e.req_headers, e.status, e.resp_headers, e.resp_body, e.duration_ms) == (
        "a=1",
        {"x": "y"},
        200,
        {"content-type": "text/plain"},
        b"hello",
        1.5,
    )
    assert store.get_tape("demo").entry_count == 2


def test_big_and_binary_bodies_survive_compression(store):
    tape = store.create_tape("big")
    big = bytes(range(256)) * 100  # 25 KB of binary
    store.add_entries(tape.id, [make_entry(0, body=big), make_entry(1, body="héllo ✓".encode())])
    back = store.get_entries(tape.id)
    assert back[0].resp_body == big
    assert back[1].resp_body.decode() == "héllo ✓"


def test_duplicate_names_are_rejected(store):
    store.create_tape("a")
    with pytest.raises(TapeExistsError):
        store.create_tape("a")


def test_missing_tape(store):
    with pytest.raises(TapeNotFoundError):
        store.get_tape("nope")


def test_delete_removes_entries_too(store):
    tape = store.create_tape("gone")
    store.add_entries(tape.id, [make_entry(0)])
    store.delete_tape("gone")
    assert store.list_tapes() == []
    assert store.conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0


def test_list_shows_counts_in_creation_order(store):
    store.create_tape("a")
    b = store.create_tape("b")
    store.add_entries(b.id, [make_entry(0), make_entry(1), make_entry(2)])
    assert [(t.name, t.entry_count) for t in store.list_tapes()] == [("a", 0), ("b", 3)]
