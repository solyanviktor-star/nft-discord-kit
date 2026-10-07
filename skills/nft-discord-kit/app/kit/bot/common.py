"""Helpers shared by the Discord modules.

Checks here are duck-typed (they read `.roles`, `.guild_permissions`, `.id`) instead of using
isinstance on discord.py classes, so the test suite can drive the handlers with light stand-ins.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..config import Settings
from ..service import MemberInfo

if TYPE_CHECKING:
    from .client import KitBot


def member_info(member: Any) -> MemberInfo:
    """What the core needs to know about a member: id, name, role ids and role names."""
    return MemberInfo(str(member.id), member.name, frozenset(str(r.id) for r in member.roles),
                      frozenset(r.name for r in member.roles))


def as_member(user: Any) -> Any | None:
    """The user as a server member (with roles), or None in a DM."""
    return user if getattr(user, "roles", None) is not None else None


def role_key(name: str) -> str:
    """A role name for matching: whole name, any case, emojis and separators at either end ignored
    ("Mod" matches "mod" and "\U0001F6E1 Mod", not "Model Citizen")."""
    key = re.sub(r"^[^0-9a-z]+|[^0-9a-z]+$", "", name.casefold())
    return re.sub(r"\s+", " ", key) or name.casefold()


def _has_role(member: Any, names: tuple[str, ...]) -> bool:
    keys = {role_key(n) for n in names}
    return bool(keys) and any(role_key(r.name) in keys for r in getattr(member, "roles", ()))


def is_admin(bot: KitBot, member: Any) -> bool:
    """Manage Server / Administrator, or a staff role from config (discord.staff_roles)."""
    if as_member(member) is None:
        return False
    perms = member.guild_permissions
    return bool(perms.manage_guild or perms.administrator) or _has_role(member, bot.s.discord.staff_roles)


def can_run_raffles(bot: KitBot, member: Any) -> bool:
    """Staff, plus raffle managers (role names) and raffle_users (ids), who get the raffle commands only."""
    if getattr(member, "id", None) in bot.s.discord.raffle_users:
        return True
    return is_admin(bot, member) or _has_role(member, bot.s.discord.raffle_manager_roles)


def is_support_staff(bot: KitBot, member: Any) -> bool:
    return is_admin(bot, member) or _has_role(member, bot.s.support.staff_roles)


def kinds_used(s: Settings) -> list[str]:
    """The wallet kinds the configured raffle chains ask for, in config order."""
    return list(dict.fromkeys(c.wallet_kind for c in s.raffle_chains))


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"
