import json

from tapedeck.core.matcher import Matcher, body_fingerprint, make_keys, normalize_query, template_path
from tapedeck.core.models import Entry


def entry(method, path, query="", body=b"", status=200, name=""):
    return Entry(method=method, path=path, query=query, req_body=body, status=status, resp_body=name.encode())


def test_template_path_replaces_ids_only():
    assert template_path("/videos/123/clips") == "/videos/{id}/clips"
    assert template_path("/u/550e8400-e29b-41d4-a716-446655440000") == "/u/{id}"
    assert template_path("/o/507f1f77bcf86cd799439011") == "/o/{id}"
    assert template_path("/items/latest") == "/items/latest"
    assert template_path("/v2/items") == "/v2/items"  # 'v2' is not a bare number


def test_query_is_sorted_and_noise_is_dropped():
    assert normalize_query("b=2&a=1&_=99999&ts=5") == "a=1&b=2"
    assert normalize_query("") == ""


def test_body_fingerprint_ignores_key_order_and_noise_fields():
    a = json.dumps({"x": 1, "y": 2, "timestamp": 111}).encode()
    b = json.dumps({"y": 2, "timestamp": 999, "x": 1}).encode()
    c = json.dumps({"x": 1, "y": 3}).encode()
    assert body_fingerprint(a) == body_fingerprint(b) != body_fingerprint(c)
    assert body_fingerprint(b"") == ""


def test_keys_get_looser_in_order():
    k = make_keys("get", "/videos/5", "page=1", b"")
    assert k.exact != k.shape != k.loose
    assert k.loose == "GET /videos/{id}"


def test_exact_then_shape_then_loose():
    m = Matcher([entry("GET", "/items/1", "page=1", name="one"), entry("GET", "/items/2", name="two-plain")])

    hit = m.find("GET", "/items/1", "page=1")
    assert (hit.level, hit.entry.resp_body) == ("exact", b"one")

    hit = m.find("GET", "/items/77", "page=1")  # other id, same shape
    assert (hit.level, hit.entry.resp_body) == ("shape", b"one")

    hit = m.find("GET", "/items/77", "page=9&x=1")  # other query: only the path shape is left
    assert hit.level == "loose"


def test_method_must_match():
    m = Matcher([entry("GET", "/items")])
    assert m.find("POST", "/items") is None


def test_unknown_path_returns_none():
    assert Matcher([entry("GET", "/items")]).find("GET", "/nope") is None


def test_same_question_returns_answers_in_order_then_repeats_last():
    m = Matcher([entry("GET", "/cart", name="empty"), entry("GET", "/cart", name="one item")])
    got = [m.find("GET", "/cart").entry.resp_body for _ in range(4)]
    assert got == [b"empty", b"one item", b"one item", b"one item"]
    m.reset()
    assert m.find("GET", "/cart").entry.resp_body == b"empty"


def test_post_bodies_are_part_of_the_match():
    m = Matcher(
        [entry("POST", "/search", body=b'{"q":"a"}', name="A"), entry("POST", "/search", body=b'{"q":"b"}', name="B")]
    )
    assert m.find("POST", "/search", body=b'{"q":"b"}').entry.resp_body == b"B"
    assert m.find("POST", "/search", body=b'{"q":"a"}').entry.resp_body == b"A"
