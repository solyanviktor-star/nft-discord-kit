"""The persistent panels (verify, support tickets, self-roles) as plain Discord message payloads.

`kit.setup panels` posts them over REST; the running bot answers their buttons through the custom
ids below, so a panel keeps working across restarts and redeploys.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from .config import Settings

VERIFY_ID = "kit:verify"
WALLETS_ID = "kit:wallets"
TICKET_PREFIX = "kit:ticket:"  # + category slug
TICKET_CLOSE_ID = "kit:ticket-close"
ROLE_PREFIX = "kit:role:"  # + role slug
ENTER_PREFIX = "kit:enter:"  # + raffle id
RAFFLE_WALLET_PREFIX = "kit:rwallet:"  # + raffle id
SUCCESS, SECONDARY, DANGER = 3, 2, 4


def slugify(text: str, default: str = "item") -> str:
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


def verify_panel(s: Settings, role_ids: Mapping[str, str]) -> dict[str, Any]:
    lines = [f"{role_mention(t.name, role_ids)}: {t.min}+ NFT{'' if t.min == 1 else 's'}" for t in s.tiers]
    lines += [f"{role_mention(c.role, role_ids)}: holds {c.name}" for c in s.collections if c.role]
    lines += [f"{role_mention(sp.role, role_ids)}: holds a {sp.name} token"
              + (f" (+{sp.bonus_tickets} raffle tickets)" if sp.bonus_tickets else "") for sp in s.specials]
    wallets = "EVM wallets (MetaMask, Rabby, Coinbase Wallet...)" + (" and Solana wallets (Phantom, Solflare, "
                                                                      "Backpack)" if s.solana else "")
    desc = (f"Link your wallet once: your roles come straight from the blockchain and stay in sync "
            f"(re-checked every {s.sync_minutes} minutes).\n\n"
            "**1.** Press **Verify** below.\n"
            "**2.** Open your personal link, connect your wallet and sign a free message. "
            "It is not a transaction and costs nothing.\n"
            "**3.** Done: your roles appear within seconds.\n\n"
            f"Works with {wallets}.\n\n**Roles**\n" + "\n".join(lines)
            + "\n\n**My Wallets** shows what is linked, unlinks a wallet or sets where giveaway prizes go.")
    return {"embeds": [{"title": f"Verify your {s.project.name} NFTs", "description": desc[:4096],
                        "color": s.project.color, "footer": {"text": f"{s.project.name} · on-chain verification"}}],
            "components": rows([button("Verify", VERIFY_ID, SUCCESS), button("My Wallets", WALLETS_ID)])}


def tickets_panel(s: Settings) -> dict[str, Any]:
    once = "One open ticket per person. " if s.support.max_open == 1 else ""
    desc = ("Need help, a prize delivery, a partnership or anything else? Pick a category below: a private "
            "thread opens for you and the team, and nobody else sees it.\n\n"
            f"{once}Close it with the button once it is solved.")
    buttons = [button(c.name, TICKET_PREFIX + slugify(c.name, "other"), SECONDARY, c.emoji) for c in s.support.categories]
    return {"embeds": [{"title": "Support tickets", "description": desc, "color": s.project.color,
                        "footer": {"text": f"{s.project.name} · support"}}],
            "components": rows(buttons)}


def self_roles_panel(s: Settings) -> dict[str, Any]:
    lines = [f"{r.emoji} **{r.name}**" + (f": {r.description}" if r.description else "") for r in s.self_roles]
    desc = "Tap a button to take a role; tap it again to drop it.\n\n" + "\n".join(lines)
    buttons = [button(r.name, ROLE_PREFIX + slugify(r.name), SECONDARY, r.emoji) for r in s.self_roles]
    return {"embeds": [{"title": "Pick your roles", "description": desc[:4096], "color": s.project.color}],
            "components": rows(buttons)}
