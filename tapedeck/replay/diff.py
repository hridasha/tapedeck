"""Compare two JSON values and say exactly where they differ.

    expected: {"id": 1, "price": 49.0, "tags": ["a", "b"]}
    actual:   {"id": 1, "price": 50.0, "tags": ["a"],  "new": true}
    ->  $.price  changed  49.0 -> 50.0
        $.tags[1] missing
        $.new    extra

`ignore` lists paths to skip (`$.items[].created_at`: list positions are written `[]`). A changed value is
"volatile", and does not count as a failure, when both the old and the new value look like a timestamp or UUID.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from tapedeck.core.noise import looks_noisy, normalize_path

MISSING = "missing"  # in the recording, not in the new answer
EXTRA = "extra"  # in the new answer, not in the recording
CHANGED = "changed"
TYPE = "type"


@dataclass
class Diff:
    path: str
    kind: str
    expected: object = None
    actual: object = None
    volatile: bool = False  # changed, but only in the way timestamps and ids always do


def is_ignored(path: str, ignore: Iterable[str]) -> bool:
    n = normalize_path(path)
    for pattern in ignore:
        p = normalize_path(pattern)
        if n == p or n.startswith(p + ".") or n.startswith(p + "[]"):
            return True
    return False


def _same_kind(a: object, b: object) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return True
    return type(a) is type(b)


def json_diff(
    expected: object, actual: object, ignore: Iterable[str] = (), *, forgive_volatile: bool = True
) -> list[Diff]:
    ignore = tuple(ignore)
    out: list[Diff] = []

    def walk(exp: object, act: object, path: str) -> None:
        if is_ignored(path, ignore):
            return
        if isinstance(exp, dict) and isinstance(act, dict):
            for key in exp.keys() - act.keys():
                if not is_ignored(f"{path}.{key}", ignore):
                    out.append(Diff(f"{path}.{key}", MISSING, expected=exp[key]))
            for key in act.keys() - exp.keys():
                if not is_ignored(f"{path}.{key}", ignore):
                    out.append(Diff(f"{path}.{key}", EXTRA, actual=act[key]))
            for key in exp.keys() & act.keys():
                walk(exp[key], act[key], f"{path}.{key}")
        elif isinstance(exp, list) and isinstance(act, list):
            for i in range(min(len(exp), len(act))):
                walk(exp[i], act[i], f"{path}[{i}]")
            for i in range(len(act), len(exp)):
                if not is_ignored(f"{path}[{i}]", ignore):
                    out.append(Diff(f"{path}[{i}]", MISSING, expected=exp[i]))
            for i in range(len(exp), len(act)):
                if not is_ignored(f"{path}[{i}]", ignore):
                    out.append(Diff(f"{path}[{i}]", EXTRA, actual=act[i]))
        elif not _same_kind(exp, act):
            out.append(Diff(path, TYPE, exp, act))
        elif exp != act:
            volatile = forgive_volatile and looks_noisy(exp) and looks_noisy(act)
            out.append(Diff(path, CHANGED, exp, act, volatile=volatile))

    walk(expected, actual, "$")
    return sorted(out, key=lambda d: d.path)


def failing(diffs: list[Diff]) -> list[Diff]:
    return [d for d in diffs if not d.volatile]
