"""Holdings readings and what follows from them: the roles a member should wear and their raffle tickets.

A reading may be partial: when the node did not answer for some wallets or special tokens, those
parts are marked unknown, and the roles that depend on them are left alone. "The node was down"
must never look like "they sold everything".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .config import Settings


@dataclass(frozen=True)
class Holdings:
    counts: Mapping[str, int] = field(default_factory=dict)  # collection name -> tokens held
    specials: Mapping[str, int] = field(default_factory=dict)  # special set name -> tokens of the set held
    complete: bool = True  # every collection answered for every wallet
    specials_known: frozenset[str] = frozenset()  # special sets read successfully
    at: int = 0

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def merged_with(self, prev: Holdings | None) -> Holdings:
        """This reading with its unknown parts filled from the previous one."""
        if prev is None:
            return self
        names = set(self.specials) | set(prev.specials) | set(self.specials_known)
        specials = {n: (self.specials.get(n, 0) if n in self.specials_known else prev.specials.get(n, 0))
                    for n in names}
        return Holdings(self.counts if self.complete else prev.counts, specials, self.complete or prev.complete,
                        self.specials_known | prev.specials_known, self.at)

    def to_dict(self) -> dict[str, Any]:
        return {"counts": dict(self.counts), "specials": dict(self.specials), "complete": self.complete,
                "specials_known": sorted(self.specials_known), "at": self.at}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Holdings:
        return cls(dict(d.get("counts") or {}), dict(d.get("specials") or {}), bool(d.get("complete", True)),
                   frozenset(d.get("specials_known") or ()), int(d.get("at") or 0))


def wanted_roles(h: Holdings, s: Settings) -> set[str]:
    """Role names this reading earns: tiers by total, collection roles, special roles."""
    want = {t.name for t in s.tiers if h.total >= t.min}
    want |= {c.role for c in s.collections if c.role and h.counts.get(c.name, 0) > 0}
    want |= {sp.role for sp in s.specials if h.specials.get(sp.name, 0) > 0}
    return want


def role_changes(h: Holdings, s: Settings, have: set[str]) -> tuple[set[str], set[str]]:
    """(add, remove) role names. Only kit-managed roles move; roles this reading cannot vouch for stay."""
    managed = {sp.role for sp in s.specials if sp.name in h.specials_known}
    if h.complete:
        managed |= {t.name for t in s.tiers} | {c.role for c in s.collections if c.role}
    have_cf = {name.casefold() for name in have}
    current = {role for role in managed if role.casefold() in have_cf}
    want = wanted_roles(h, s) & managed
    return want - current, current - want


def ticket_count(h: Holdings | None, s: Settings) -> tuple[int, list[str]]:
    """Raffle tickets: tickets_per_token x min(total, cap) + the special bonus ("best" one or the "sum").

    Returns (tickets, labels explaining the number), (0, []) when nothing is held.
    """
    if h is None or h.total <= 0:
        return 0, []
    r = s.raffles
    total = h.total
    labels = [f"{total} NFT{'' if total == 1 else 's'}" + (f" (counted up to {r.ticket_cap})" if total > r.ticket_cap
                                                         else "")]
    bonuses = [(sp.name, sp.bonus_tickets) for sp in s.specials if sp.bonus_tickets and h.specials.get(sp.name, 0) > 0]
    if bonuses and r.special_bonus == "best":
        bonuses = [max(bonuses, key=lambda b: b[1])]
    labels += [f"{name} +{n}" for name, n in bonuses]
    return min(total, r.ticket_cap) * r.tickets_per_token + sum(n for _, n in bonuses), labels


def tickets_rule(s: Settings) -> str:
    """One paragraph explaining how tickets are counted (shown on raffle cards)."""
    r = s.raffles
    per = f"{r.tickets_per_token} ticket{'' if r.tickets_per_token == 1 else 's'}"
    text = f"{per} per NFT in your linked wallets, up to {r.tickets_per_token * r.ticket_cap}."
    bonus = [f"{sp.name} +{sp.bonus_tickets}" for sp in s.specials if sp.bonus_tickets]
    if bonus:
        text += ("\nBonus (the best one): " if r.special_bonus == "best" else "\nBonus (all that apply): ")
        text += " / ".join(bonus)
    if not r.require_holding:
        text += "\nNo NFT needed to enter: everyone gets at least 1 ticket."
    return text
