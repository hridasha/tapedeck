"""Tapes live in one SQLite file. Big bodies are compressed."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
import zlib
from collections.abc import Iterable
from pathlib import Path

from .models import Entry, Tape

SCHEMA = """
CREATE TABLE IF NOT EXISTS tapes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL,
    source      TEXT NOT NULL,
    target_url  TEXT NOT NULL DEFAULT '',
    noise       TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tape_id     INTEGER NOT NULL REFERENCES tapes(id) ON DELETE CASCADE,
    ord         INTEGER NOT NULL,
    method      TEXT NOT NULL,
    path        TEXT NOT NULL,
    query       TEXT NOT NULL DEFAULT '',
    req_headers TEXT NOT NULL DEFAULT '{}',
    req_body    BLOB NOT NULL,
    status      INTEGER NOT NULL,
    resp_headers TEXT NOT NULL DEFAULT '{}',
    resp_body   BLOB NOT NULL,
    duration_ms REAL NOT NULL DEFAULT 0,
    started_at  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_entries_tape ON entries(tape_id, ord);
"""

COMPRESS_OVER = 1024  # bytes


class TapeExistsError(Exception):
    pass


class TapeNotFoundError(KeyError):
    pass


def _pack(data: bytes) -> bytes:
    if len(data) > COMPRESS_OVER:
        return b"Z" + zlib.compress(data, 6)
    return b"R" + data


def _unpack(blob: bytes) -> bytes:
    blob = bytes(blob)
    return zlib.decompress(blob[1:]) if blob[:1] == b"Z" else blob[1:]


class Storage:
    def __init__(self, path: str | Path = "tapedeck.db"):
        self.path = str(path)
        # The recorder and the web API write from worker threads, so one lock guards every write.
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.execute("PRAGMA foreign_keys = ON")
            self.conn.executescript(SCHEMA)
            cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(tapes)")}
            if "noise" not in cols:  # tapes created by version 0.1
                self.conn.execute("ALTER TABLE tapes ADD COLUMN noise TEXT NOT NULL DEFAULT '[]'")
                self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Storage:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ tapes
    def create_tape(self, name: str, source: str = "manual", target_url: str = "") -> Tape:
        created = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        with self.lock:
            try:
                cur = self.conn.execute(
                    "INSERT INTO tapes (name, created_at, source, target_url) VALUES (?, ?, ?, ?)",
                    (name, created, source, target_url),
                )
            except sqlite3.IntegrityError as exc:
                raise TapeExistsError(f"a tape called '{name}' already exists") from exc
            self.conn.commit()
        return Tape(id=cur.lastrowid, name=name, created_at=created, source=source, target_url=target_url)

    def get_tape(self, name: str) -> Tape:
        row = self.conn.execute(
            "SELECT t.*, (SELECT COUNT(*) FROM entries e WHERE e.tape_id = t.id) AS n FROM tapes t WHERE name = ?",
            (name,),
        ).fetchone()
        if row is None:
            raise TapeNotFoundError(name)
        return self._tape(row)

    def list_tapes(self) -> list[Tape]:
        rows = self.conn.execute(
            "SELECT t.*, (SELECT COUNT(*) FROM entries e WHERE e.tape_id = t.id) AS n FROM tapes t ORDER BY t.id"
        ).fetchall()
        return [self._tape(r) for r in rows]

    def delete_tape(self, name: str) -> None:
        tape = self.get_tape(name)
        with self.lock:
            self.conn.execute("DELETE FROM tapes WHERE id = ?", (tape.id,))
            self.conn.commit()

    @staticmethod
    def _tape(row: sqlite3.Row) -> Tape:
        return Tape(
            id=row["id"],
            name=row["name"],
            created_at=row["created_at"],
            source=row["source"],
            target_url=row["target_url"],
            entry_count=row["n"],
        )

    # ---------------------------------------------------------------- entries
    def add_entries(self, tape_id: int, entries: Iterable[Entry]) -> int:
        rows = [
            (
                tape_id,
                e.order,
                e.method,
                e.path,
                e.query,
                json.dumps(e.req_headers),
                _pack(e.req_body),
                e.status,
                json.dumps(e.resp_headers),
                _pack(e.resp_body),
                e.duration_ms,
                e.started_at,
            )
            for e in entries
        ]
        with self.lock:
            self.conn.executemany(
                "INSERT INTO entries (tape_id, ord, method, path, query, req_headers, req_body, status,"
                " resp_headers, resp_body, duration_ms, started_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            self.conn.commit()
        return len(rows)

    def next_order(self, tape_id: int) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(ord) + 1, 0) AS n FROM entries WHERE tape_id = ?", (tape_id,)
        ).fetchone()
        return row["n"]

    # ------------------------------------------------------------------ noise
    def get_noise(self, name: str) -> list[str]:
        row = self.conn.execute("SELECT noise FROM tapes WHERE name = ?", (name,)).fetchone()
        if row is None:
            raise TapeNotFoundError(name)
        return json.loads(row["noise"])

    def set_noise(self, name: str, paths: Iterable[str]) -> None:
        self.get_tape(name)
        with self.lock:
            self.conn.execute("UPDATE tapes SET noise = ? WHERE name = ?", (json.dumps(sorted(set(paths))), name))
            self.conn.commit()

    def get_entry(self, tape_id: int, entry_id: int) -> Entry | None:
        return next((e for e in self.get_entries(tape_id) if e.id == entry_id), None)

    def get_entries(self, tape_id: int) -> list[Entry]:
        rows = self.conn.execute("SELECT * FROM entries WHERE tape_id = ? ORDER BY ord, id", (tape_id,)).fetchall()
        return [
            Entry(
                id=r["id"],
                order=r["ord"],
                method=r["method"],
                path=r["path"],
                query=r["query"],
                req_headers=json.loads(r["req_headers"]),
                req_body=_unpack(r["req_body"]),
                status=r["status"],
                resp_headers=json.loads(r["resp_headers"]),
                resp_body=_unpack(r["resp_body"]),
                duration_ms=r["duration_ms"],
                started_at=r["started_at"],
            )
            for r in rows
        ]
