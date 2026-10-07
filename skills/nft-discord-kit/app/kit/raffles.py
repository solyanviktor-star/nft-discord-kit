"""Raffles (giveaways) without Discord: the data model, the entry rules, the draw and the texts.

The Enter button and the website both go through `kit.service.Kit.enter`, so the checks, the
entry shape and the replies are the same everywhere.
"""
from __future__ import annotations

import csv
import io
import random
import re
import secrets
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Sequence

OPEN, ENDED, CANCELLED = "open", "ended", "cancelled"
# Cards are redrawn on a schedule, not on every entry: Discord budgets edits of messages older than
# an hour (error 30046), and a wave of entries would otherwise edit one message hundreds of times.
CARD_YOUNG_GAP = 180  # a card younger than an hour: at most every 3 minutes
CARD_OLD_GAP = 900  # older than an hour: at most every 15 minutes
DISCORD_EPOCH_MS = 1420070400000
WINNERS_NOTE = "Congratulations! Your wallet is submitted as entered. Mint details will be posted by the team."


@dataclass
class Raffle:
    id: str
    title: str
    link: str
    chain: str
    wallet_kind: str
    gtd: int
    fcfs: int
    ends: int
    created: int
    created_by: str
    channel_id: str
    eligible: list[str] = field(default_factory=list)  # role ids; empty = anyone in the server
    description: str = ""
    image: str = ""
    message_id: str = ""
    status: str = OPEN
    card_gone: str = ""  # the card message id that answered 404: no more redraws for it
    result: dict[str, Any] = field(default_factory=dict)  # gtd, fcfs, wallets, at, winners_msg, rerolls

    @property
    def winners(self) -> int:
        return self.gtd + self.fcfs

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Raffle:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Entry:
    raffle_id: str
    user_id: str
    user_name: str
    wallet: dict[str, str]  # {"address": ...} for evm/solana; the custom kind's fields otherwise
    tickets: int
    at: int


def new_raffle_id(taken: Iterable[str]) -> str:
    taken = set(taken)
    while True:
        rid = secrets.token_hex(3)
        if rid not in taken:
            return rid


_UNITS = {"w": 604800, "d": 86400, "h": 3600, "m": 60}


def parse_duration(text: str) -> int:
    """'24h', '2d 12h', '90m', '1w' -> seconds (0 when nothing parses)."""
    return sum(int(n) * _UNITS[u] for n, u in re.findall(r"(\d+)\s*([wdhm])", (text or "").lower()))


def weighted_pick(pool: Mapping[str, int], n: int, rng: random.Random | None = None) -> list[str]:
    """n distinct winners, probability proportional to tickets (integer weights, no repeats)."""
    rng = rng or random.SystemRandom()
    left = {uid: int(w) for uid, w in pool.items() if int(w) > 0}
    out: list[str] = []
    while left and len(out) < n:
        x = rng.randrange(sum(left.values()))
        for uid, w in left.items():
            if x < w:
                out.append(uid)
                del left[uid]
                break
            x -= w
    return out


def refusal(r: Raffle | None, now: float, drawing: bool, member_role_ids: Iterable[str] | None) -> str | None:
    """The checks that need no chain read: closed, closing, not_member, not_eligible (or None)."""
    if r is None or r.status != OPEN:
        return "closed"
    if drawing or r.ends <= now:
        return "closing"
    if member_role_ids is None:
        return "not_member"
    if r.eligible and not set(member_role_ids) & set(r.eligible):
        return "not_eligible"
    return None


ENTRY_MESSAGES = {
    "closed": "This raffle has ended.",
    "closing": "This raffle is closing right now.",
    "not_member": "Join the Discord server first, then enter.",
    "not_eligible": "Only holders can enter. Verify in {verify} to get your role — it is automatic after that.",
    "no_profile": "Link your wallet once and you are in for every raffle after that.",
    "rpc_busy": "Chain RPC is busy - try again in a minute.",
    "no_pass": ("No {project} NFT found in your linked wallets — if yours sits in another wallet, link THAT "
                "wallet too and press Verify."),
    "no_evm_wallet": "No EVM wallet linked — link one first.",
    "no_wallet": "This raffle is on {chain} — add your {kind} wallet first.",
    "entered": "You're in! **{tickets}** ({parts}) · wallet `{wallet}`",
    "updated": "You're already in ✅ · **{tickets}** ({parts}) · wallet `{wallet}`",
}


class _Blank(dict):
    def __missing__(self, key: str) -> str:
        return ""


def entry_message(code: str, **kw: Any) -> str:
    """The reply for an entry result code."""
    tickets = kw.get("tickets")
    if isinstance(tickets, int):
        kw["tickets"] = f"{tickets} ticket{'' if tickets == 1 else 's'}"
    return ENTRY_MESSAGES.get(code, "Something went wrong.").format_map(_Blank(kw))


def snowflake_time(snowflake: str | int) -> float:
    """Unix seconds a Discord id was created at; 0 for a bad id."""
    try:
        return ((int(snowflake) >> 22) + DISCORD_EPOCH_MS) / 1000
    except (TypeError, ValueError):
        return 0.0


def redraw_due(now: float, last_redraw: float, card_created: float) -> bool:
    """May a card be edited again? Young cards every 3 minutes, cards older than an hour every 15."""
    gap = CARD_OLD_GAP if now - card_created > 3600 else CARD_YOUNG_GAP
    return now - last_redraw >= gap


def mention_chunks(user_ids: Sequence[str], limit: int = 1900) -> list[str]:
    """User mentions packed under Discord's 2000-character message limit (100 winners do not fit in one)."""
    chunks, cur = [], ""
    for uid in user_ids:
        m = f"<@{uid}>"
        if cur and len(cur) + 1 + len(m) > limit:
            chunks.append(cur)
            cur = m
        else:
            cur = f"{cur} {m}" if cur else m
    return chunks + [cur] if cur else chunks


def short(address: str) -> str:
    """0x1234...abcd for display."""
    return address if len(address or "") <= 12 else f"{address[:6]}…{address[-4:]}"


def wallet_text(wallet: Mapping[str, str] | None) -> str:
    if not wallet:
        return "—"
    if set(wallet) == {"address"}:
        return wallet["address"]
    return " · ".join(f"{k}: {v}" for k, v in wallet.items())


def card_embed(r: Raffle, entrants: int, eligible_mentions: Sequence[str], brand: str, color: int,
               rule: str) -> dict[str, Any]:
    """The raffle card as a Discord embed dict."""
    ended = r.status != OPEN
    e: dict[str, Any] = {
        "title": (("[ENDED] " if ended else "") + r.title)[:256],
        "color": 0x555555 if ended else color,
        "description": (r.link + (f"\n\n{r.description}" if r.description else ""))[:4096],
        "fields": [
            {"name": "Ends", "value": f"<t:{r.ends}:f>\n<t:{r.ends}:R>", "inline": True},
            {"name": "Chain", "value": r.chain, "inline": True},
            {"name": "Winners", "value": str(r.winners), "inline": True},
            {"name": "Entrants", "value": str(entrants), "inline": True},
            {"name": "Guaranteed", "value": str(r.gtd), "inline": True},
            {"name": "FCFS", "value": str(r.fcfs), "inline": True},
            {"name": "Eligible Roles", "value": " ".join(eligible_mentions)[:1024] or "—", "inline": False},
            {"name": "Tickets", "value": rule[:1024], "inline": False},
        ],
        "footer": {"text": f"{brand} · {r.id}"},
    }
    if r.image:
        e["image"] = {"url": r.image}
    return e


def winners_embed(r: Raffle, gtd: Sequence[str], fcfs: Sequence[str], brand: str, title_prefix: str = "",
                  note: str = WINNERS_NOTE) -> dict[str, Any]:
    """The winners announcement. A huge list shows counts; the mentions themselves go in the message above."""
    parts = []
    if gtd and fcfs:
        parts.append("**GTD:** " + " ".join(f"<@{u}>" for u in gtd))
        parts.append("**FCFS:** " + " ".join(f"<@{u}>" for u in fcfs))
    else:
        parts.append(" ".join(f"<@{u}>" for u in (gtd or fcfs)) or "—")
    parts += ["", note]
    desc = "\n".join(parts)
    if len(desc) > 4000:  # an embed description caps at 4096
        head = " / ".join(x for x in (f"**GTD:** {len(gtd)}" if gtd else "", f"**FCFS:** {len(fcfs)}" if fcfs else "")
                          if x)
        desc = head + " winners — tagged above.\n\n" + note
    return {"title": f"WINNERS: {title_prefix}{r.title}"[:256], "color": 0x2ECC71, "description": desc,
            "fields": [{"name": "Details", "value": r.link[:1024], "inline": False}],
            "footer": {"text": f"{brand} · {r.id}"}}


def wallet_columns(kind: str, field_keys: Sequence[str] = ()) -> list[tuple[str, str]]:
    """(CSV column, wallet key): one `wallet` column for evm/solana, `wallet_<key>` per field of a custom kind."""
    if kind in ("evm", "solana"):
        return [("wallet", "address")]
    return [(f"wallet_{k}", k) for k in field_keys]


def export_csv(r: Raffle, entries: Sequence[Entry], columns: Sequence[tuple[str, str]], everyone: bool) -> str:
    """All entrants (id, name, wallet columns, tickets), or the winners (type, id, name, wallet columns).

    A raffle that has not been drawn yet always exports its entrants.
    """
    buf = io.StringIO()
    w = csv.writer(buf)
    res = r.result or {}
    if everyone or not res:
        w.writerow(["discord_id", "discord_name"] + [c for c, _ in columns] + ["tickets"])
        for e in entries:
            w.writerow([e.user_id, e.user_name] + [e.wallet.get(k, "") for _, k in columns] + [e.tickets])
    else:
        w.writerow(["type", "discord_id", "discord_name"] + [c for c, _ in columns])
        by_user = {e.user_id: e for e in entries}
        for kind in ("gtd", "fcfs"):
            for uid in res.get(kind, []):
                e = by_user.get(uid)
                wallet = (res.get("wallets") or {}).get(uid) or (e.wallet if e else {})
                w.writerow([kind.upper(), uid, e.user_name if e else ""] + [wallet.get(k, "") for _, k in columns])
    return buf.getvalue()
