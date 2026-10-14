import json

from tapedeck.core.models import Entry
from tapedeck.core.noise import detect_noise, differing_paths, flatten, looks_noisy, normalize_path


def entry(path, body, query=""):
    return Entry(method="GET", path=path, query=query, resp_body=json.dumps(body).encode())


def test_flatten_lists_every_leaf_with_its_path():
    assert flatten({"a": {"b": 1}, "c": [{"d": 2}, 3]}) == {"$.a.b": 1, "$.c[0].d": 2, "$.c[1]": 3}


def test_normalize_path_turns_indexes_into_brackets():
    assert normalize_path("$.items[3].tags[0]") == "$.items[].tags[]"


def test_looks_noisy():
    assert looks_noisy("2025-01-01T10:00:00Z")
    assert looks_noisy("2025-01-01 10:00:00.123+02:00")
    assert looks_noisy("550e8400-e29b-41d4-a716-446655440000")
    assert looks_noisy(1699999999)
    assert looks_noisy(1699999999123)
    assert not looks_noisy("hello")
    assert not looks_noisy(42)
    assert not looks_noisy(True)


def test_differing_paths_cover_changed_added_and_removed():
    a = {"x": 1, "y": {"z": 1}, "gone": 1}
    b = {"x": 1, "y": {"z": 2}, "new": 1}
    assert differing_paths(a, b) == {"$.y.z", "$.gone", "$.new"}


def test_repeated_identical_request_with_different_answers_gives_candidates():
    tape = [
        entry("/time", {"now": "A", "ok": True}),
        entry("/time", {"now": "B", "ok": True}),
        entry("/other", {"now": "ONLY ONCE"}),
    ]
    assert detect_noise(tape) == {"$.now"}


def test_list_positions_collapse_into_one_rule():
    tape = [
        entry("/l", {"items": [{"at": 1}, {"at": 2}]}),
        entry("/l", {"items": [{"at": 5}, {"at": 6}]}),
    ]
    assert detect_noise(tape) == {"$.items[].at"}


def test_different_requests_are_not_compared_with_each_other():
    assert detect_noise([entry("/a", {"v": 1}), entry("/b", {"v": 2})]) == set()
    assert detect_noise([entry("/a", {"v": 1}, "p=1"), entry("/a", {"v": 2}, "p=2")]) == set()


def test_by_pattern_flags_timestamp_like_values_seen_once():
    tape = [entry("/x", {"created_at": "2025-01-01T00:00:00Z", "name": "n"})]
    assert detect_noise(tape) == set()
    assert detect_noise(tape, by_pattern=True) == {"$.created_at"}


def test_non_json_bodies_are_ignored():
    tape = [Entry(method="GET", path="/t", resp_body=b"plain"), Entry(method="GET", path="/t", resp_body=b"other")]
    assert detect_noise(tape) == set()
