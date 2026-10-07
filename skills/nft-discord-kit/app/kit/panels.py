"""The persistent panels (verify, support tickets, self-roles) as plain Discord message payloads.

`kit.setup panels` posts them over REST and `/nft raffle panel` / `/nft tickets panel` post them from
Discord; the running bot answers their buttons through the custom ids below, so a panel keeps
working across restarts and redeploys.
"""
from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from .config import Settings

VERIFY_ID = "kit:verify"
WALLETS_ID = "kit:wallets"
TICKET_PREFIX = "tk:open:"  # + category slug
TICKET_CLOSE_ID = "tk:close"
ROLE_PREFIX = "kit:role:"  # + role slug
ENTER_PREFIX = "rf:enter:"  # + raffle id
RAFFLE_WALLET_PREFIX = "rf:wallet:"  # + raffle id
SUCCESS, SECONDARY = 3, 2


def slugify(text: str, default: str = "other") -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or default


def button(label: str, custom_id: str, style: int = SECONDARY, emoji: str = "") -> dict[str, Any]:
    b: dict[str, Any] = {"type": 2, "style": style, "label": label[:80], "custom_id": custom_id}
    if emoji:
        b["emoji"] = {"name": emoji}
    return b


def rows(buttons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"type": 1, "components": buttons[i:i + 5]} for i in range(0, min(len(buttons), 25), 5)]


def role_mention(name: str, role_ids: Mapping[str, str]) -> str:
    """<@&id> when the role id is known (it renders as a coloured pill), else the bold name."""
    rid = role_ids.get(name) or role_ids.get(name.casefold())
    return f"<@&{rid}>" if rid else f"**{name}**"


def roles_legend(s: Settings, role_ids: Mapping[str, str]) -> str:
    """The roles the kit gives: count tiers first, then collection and special roles."""
    lines = [f"{role_mention(t.name, role_ids)} — {t.min}+ NFT{'' if t.min == 1 else 's'}" for t in s.tiers]
    lines += [f"{role_mention(c.role, role_ids)} — holds {c.name}" for c in s.collections if c.role]
    lines += [f"{role_mention(sp.role, role_ids)} — holds a {sp.name} token" for sp in s.specials]
    return "\n".join(lines) + "\n"


def verify_panel(s: Settings, role_ids: Mapping[str, str]) -> dict[str, Any]:
    site = urlsplit(s.project.public_url).netloc or "the verification page"
    other = [s.kind_label(k) for k in dict.fromkeys(c.wallet_kind for c in s.raffle_chains) if k != "evm"]
    desc = ("Link your wallet once — roles come straight from the blockchain, in seconds, and stay in sync "
            f"automatically (re-checked every {s.sync_minutes} minutes).\n\n"
            "**1.** Press **Verify** below\n"
            f"**2.** Sign once on {site} (free message, not a transaction)\n"
            "**3.** Done — your roles land within seconds (press **Verify** again any time to re-check)\n\n"
            "**Roles**\n" + roles_legend(s, role_ids)
            + "\nThe same wallet is used for raffle entries."
            + (f" {' and '.join(other)} wallets for those raffles — via **My Wallets**." if other else ""))
    return {"embeds": [{"title": f"Verify your {s.project.name} holdings", "description": desc[:4096],
                        "color": s.project.color, "footer": {"text": f"{s.project.name} · on-chain verification"}}],
            "components": rows([button("Verify", VERIFY_ID, SUCCESS), button("My Wallets", WALLETS_ID)])}


def tickets_panel(s: Settings) -> dict[str, Any]:
    desc = ("Need help, a prize delivery, a partnership or anything else? Pick a category below: a private thread "
            "opens here for you and the team, nobody else sees it.\n\n"
            + ("One open ticket per person. " if s.support.max_open == 1 else "")
            + "Close it with the button once it is solved.")
    buttons = [button(c.name, TICKET_PREFIX + slugify(c.name), SECONDARY, c.emoji) for c in s.support.categories[:5]]
    return {"embeds": [{"title": "Support tickets", "description": desc, "color": s.project.color,
                        "footer": {"text": f"{s.project.name} · support"}}],
            "components": rows(buttons)}


def self_roles_panel(s: Settings) -> dict[str, Any]:
    lines = [f"{r.emoji} **{r.name}**" + (f" — {r.description}" if r.description else "") for r in s.self_roles]
    desc = "Tap a button to take a role; tap it again to drop it.\n\n" + "\n".join(lines)
    buttons = [button(r.name, ROLE_PREFIX + slugify(r.name, "role"), SECONDARY, r.emoji) for r in s.self_roles]
    return {"embeds": [{"title": "Claim your roles", "description": desc[:4096], "color": s.project.color}],
            "components": rows(buttons)}
