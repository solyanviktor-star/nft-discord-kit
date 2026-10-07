"""SQLite storage (stdlib sqlite3) in data/kit.db.

Everything runs in one asyncio loop and every method here is synchronous, so a check followed by a
write inside one method cannot interleave with another request.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .holdings import Holdings
from .raffles import Entry, Raffle

SCHEMA = """
CREATE TABLE IF NOT EXISTS wallets (
    kind TEXT NOT NULL, address TEXT NOT NULL, user_id TEXT NOT NULL, user_name TEXT NOT NULL DEFAULT '',
    linked_at INTEGER NOT NULL, PRIMARY KEY (kind, address));
CREATE INDEX IF NOT EXISTS wallets_by_user ON wallets (user_id);
CREATE TABLE IF NOT EXISTS raffle_wallets (
    user_id TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL, updated_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, kind));
CREATE TABLE IF NOT EXISTS holdings (user_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS raffles (
    id TEXT PRIMARY KEY, status TEXT NOT NULL, ends INTEGER NOT NULL, created INTEGER NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS entries (
    raffle_id TEXT NOT NULL, user_id TEXT NOT NULL, user_name TEXT NOT NULL, wallet TEXT NOT NULL,
    tickets INTEGER NOT NULL, at INTEGER NOT NULL, PRIMARY KEY (raffle_id, user_id));
CREATE TABLE IF NOT EXISTS support_tickets (
    thread_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@dataclass(frozen=True)
class Wallet:
    kind: str
    address: str
    user_id: str
    linked_at: int


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # wallets ---------------------------------------------------------------------------------------
    def link_wallet(self, kind: str, address: str, user_id: str, user_name: str, now: int) -> str | None:
        """Link a wallet to a Discord account (moving it if another account had it); returns that other account."""
        row = self.db.execute("SELECT user_id FROM wallets WHERE kind=? AND address=?", (kind, address)).fetchone()
        self.db.execute(
            "INSERT INTO wallets (kind, address, user_id, user_name, linked_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (kind, address) DO UPDATE SET user_id=excluded.user_id, user_name=excluded.user_name, "
            "linked_at=excluded.linked_at", (kind, address, user_id, user_name, now))
        previous = row["user_id"] if row else None
        return previous if previous and previous != user_id else None

    def unlink_wallet(self, kind: str, address: str, user_id: str) -> bool:
        cur = self.db.execute("DELETE FROM wallets WHERE kind=? AND address=? AND user_id=?", (kind, address, user_id))
        return cur.rowcount > 0

    def wallets_of(self, user_id: str) -> list[Wallet]:
        """Newest first."""
        rows = self.db.execute("SELECT kind, address, user_id, linked_at FROM wallets WHERE user_id=? "
                               "ORDER BY linked_at DESC, rowid DESC", (user_id,))
        return [Wallet(r["kind"], r["address"], r["user_id"], r["linked_at"]) for r in rows]

    def owners(self, user_ids: list[str] | None = None) -> dict[str, list[tuple[str, str]]]:
        """user id -> [(kind, address)] for the given users (every linked user when None)."""
        out: dict[str, list[tuple[str, str]]] = {uid: [] for uid in user_ids or []}
        rows = self.db.execute("SELECT user_id, kind, address FROM wallets ORDER BY linked_at")
        for r in rows:
            if user_ids is None or r["user_id"] in out:
                out.setdefault(r["user_id"], []).append((r["kind"], r["address"]))
        return out

    # raffle wallets ---------------------------------------------------------------------------------
    def set_raffle_wallet(self, user_id: str, kind: str, value: dict[str, str] | None, now: int) -> None:
        if value is None:
            self.db.execute("DELETE FROM raffle_wallets WHERE user_id=? AND kind=?", (user_id, kind))
        else:
            self.db.execute("INSERT OR REPLACE INTO raffle_wallets (user_id, kind, value, updated_at) "
                            "VALUES (?, ?, ?, ?)", (user_id, kind, json.dumps(value), now))

    def raffle_wallet(self, user_id: str, kind: str) -> dict[str, str] | None:
        row = self.db.execute("SELECT value FROM raffle_wallets WHERE user_id=? AND kind=?", (user_id, kind)).fetchone()
        return json.loads(row["value"]) if row else None

    # holdings ---------------------------------------------------------------------------------------
    def save_holdings(self, user_id: str, h: Holdings) -> None:
        self.db.execute("INSERT OR REPLACE INTO holdings (user_id, data) VALUES (?, ?)",
                        (user_id, json.dumps(h.to_dict())))

    def holdings(self, user_id: str) -> Holdings | None:
        row = self.db.execute("SELECT data FROM holdings WHERE user_id=?", (user_id,)).fetchone()
        return Holdings.from_dict(json.loads(row["data"])) if row else None

    # raffles ----------------------------------------------------------------------------------------
    def save_raffle(self, r: Raffle) -> None:
        self.db.execute("INSERT OR REPLACE INTO raffles (id, status, ends, created, data) VALUES (?, ?, ?, ?, ?)",
                        (r.id, r.status, r.ends, r.created, json.dumps(r.to_dict())))

    def raffle(self, raffle_id: str) -> Raffle | None:
        row = self.db.execute("SELECT data FROM raffles WHERE id=?", (raffle_id,)).fetchone()
        return Raffle.from_dict(json.loads(row["data"])) if row else None

    def raffles(self, status: str | None = None, limit: int = 500) -> list[Raffle]:
        """Newest first."""
        sql, args = "SELECT data FROM raffles", ()
        if status:
            sql, args = sql + " WHERE status=?", (status,)
        rows = self.db.execute(sql + " ORDER BY created DESC LIMIT ?", (*args, limit))
        return [Raffle.from_dict(json.loads(r["data"])) for r in rows]

    def delete_raffle(self, raffle_id: str) -> None:
        """Only for a raffle whose card could not be posted (nobody saw it)."""
        self.db.execute("DELETE FROM entries WHERE raffle_id=?", (raffle_id,))
        self.db.execute("DELETE FROM raffles WHERE id=?", (raffle_id,))

    def raffle_ids(self) -> list[str]:
        return [r["id"] for r in self.db.execute("SELECT id FROM raffles")]

    def put_entry(self, e: Entry) -> None:
        self.db.execute("INSERT OR REPLACE INTO entries (raffle_id, user_id, user_name, wallet, tickets, at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (e.raffle_id, e.user_id, e.user_name, json.dumps(e.wallet), e.tickets, e.at))

    def entry(self, raffle_id: str, user_id: str) -> Entry | None:
        row = self.db.execute("SELECT * FROM entries WHERE raffle_id=? AND user_id=?", (raffle_id, user_id)).fetchone()
        return _entry(row) if row else None

    def entries(self, raffle_id: str) -> list[Entry]:
        """In entry order."""
        rows = self.db.execute("SELECT * FROM entries WHERE raffle_id=? ORDER BY at, rowid", (raffle_id,))
        return [_entry(r) for r in rows]

    def entry_count(self, raffle_id: str) -> int:
        return int(self.db.execute("SELECT COUNT(*) FROM entries WHERE raffle_id=?", (raffle_id,)).fetchone()[0])

    def entry_counts(self) -> dict[str, int]:
        rows = self.db.execute("SELECT raffle_id, COUNT(*) AS n FROM entries GROUP BY raffle_id")
        return {r["raffle_id"]: r["n"] for r in rows}

    def entered_raffles(self, user_id: str) -> set[str]:
        return {r["raffle_id"] for r in self.db.execute("SELECT raffle_id FROM entries WHERE user_id=?", (user_id,))}

    # support tickets --------------------------------------------------------------------------------
    def next_ticket_number(self) -> int:
        self.db.execute("INSERT INTO meta (key, value) VALUES ('ticket_seq', '1') "
                        "ON CONFLICT (key) DO UPDATE SET value = CAST(value AS INTEGER) + 1")
        return int(self.db.execute("SELECT value FROM meta WHERE key='ticket_seq'").fetchone()["value"])

    def save_ticket(self, ticket: dict[str, Any]) -> None:
        self.db.execute("INSERT OR REPLACE INTO support_tickets (thread_id, user_id, status, data) VALUES (?, ?, ?, ?)",
                        (ticket["thread"], ticket["user"], ticket["status"], json.dumps(ticket)))

    def ticket(self, thread_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT data FROM support_tickets WHERE thread_id=?", (thread_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def open_tickets(self, user_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT data FROM support_tickets WHERE user_id=? AND status='open'", (user_id,))
        return [json.loads(r["data"]) for r in rows]


def _entry(row: sqlite3.Row) -> Entry:
    return Entry(row["raffle_id"], row["user_id"], row["user_name"], json.loads(row["wallet"]), row["tickets"], row["at"])
