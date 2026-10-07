"""The server layout template (`templates/server.toml`) and the planner that compares it with a real server.

The planner only ever proposes creations: missing roles, categories, channels, missing permission
overwrites on existing channels, and guild defaults. It never deletes, renames or moves anything.
Existing things are matched by name, ignoring case and any emoji or separator in front.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .config import ConfigError, Settings

PERMISSIONS = {name: 1 << bit for bit, name in enumerate((
    "create_instant_invite", "kick_members", "ban_members", "administrator", "manage_channels", "manage_guild",
    "add_reactions", "view_audit_log", "priority_speaker", "stream", "view_channel", "send_messages",
    "send_tts_messages", "manage_messages", "embed_links", "attach_files", "read_message_history",
    "mention_everyone", "use_external_emojis", "view_guild_insights", "connect", "speak", "mute_members",
    "deafen_members", "move_members", "use_vad", "change_nickname", "manage_nicknames", "manage_roles",
    "manage_webhooks", "manage_guild_expressions", "use_application_commands", "request_to_speak",
    "manage_events", "manage_threads", "create_public_threads", "create_private_threads",
    "use_external_stickers", "send_messages_in_threads", "use_embedded_activities", "moderate_members"))}
P = PERMISSIONS
VIEW = P["view_channel"]
VOICE = P["connect"] | P["speak"]
WRITE = P["send_messages"] | P["send_messages_in_threads"] | P["create_public_threads"] | P["create_private_threads"]
BOT_BASE = VIEW | P["send_messages"] | P["embed_links"] | P["attach_files"] | P["read_message_history"]
PRESETS = ("public", "public_readonly", "holders", "holders_readonly", "staff")
CHANNEL_TYPES = {"text": 0, "voice": 2}
CATEGORY = 4
EVERYONE, BOT = "@everyone", "@bot"
GUILD_FIELDS = {
    "verification_level": ("verification_level", {"none": 0, "low": 1, "medium": 2, "high": 3, "very_high": 4}),
    "default_notifications": ("default_message_notifications", {"all_messages": 0, "only_mentions": 1}),
    "explicit_content_filter": ("explicit_content_filter",
                                {"disabled": 0, "members_without_roles": 1, "all_members": 2}),
}


@dataclass(frozen=True)
class RoleSpec:
    name: str
    color: int = 0
    hoist: bool = False
    mentionable: bool = False
    permissions: int = 0
    access: str = ""  # "staff": sees everything; "holders": sees holder channels; "": nothing extra


@dataclass(frozen=True)
class ChannelSpec:
    name: str
    type: str = "text"
    topic: str = ""
    access: str = ""  # "" = the category's preset
    roles: tuple[str, ...] = ()  # who counts as "holders" here (default: the lowest holder tier)
    bot: int = 0  # extra permissions for the bot in this channel
    allow_threads: bool = False  # members may write inside threads they are added to (ticket threads)


@dataclass(frozen=True)
class CategorySpec:
    name: str
    access: str
    channels: tuple[ChannelSpec, ...]


@dataclass(frozen=True)
class Template:
    guild: Mapping[str, int]  # Discord guild field -> value
    roles: tuple[RoleSpec, ...]
    categories: tuple[CategorySpec, ...]


@dataclass
class Action:
    kind: str  # create_role | create_category | create_channel | add_overwrites | update_guild
    name: str
    data: dict[str, Any] = field(default_factory=dict)
    parent: str = ""  # the category of a new channel

    def describe(self) -> str:
        if self.kind == "create_role":
            return f"create role      {self.name}"
        if self.kind == "create_category":
            return f"create category  {self.name}"
        if self.kind == "create_channel":
            return f"create {self.data['type_name']:<9} {self.name}  (in {self.parent})"
        if self.kind == "add_overwrites":
            return f"add permissions  {self.name}: " + ", ".join(t for t, _ in self.data["overwrites"])
        return "update server    " + ", ".join(f"{k} = {v}" for k, v in self.data.items())


@dataclass
class Plan:
    actions: list[Action]
    notes: list[str]


def core_name(name: str) -> str:
    """'🎁┃giveaways', 'Giveaways' and 'giveaways' all give 'giveaways'."""
    s = re.sub(r"[\s_]+", "-", name.strip().casefold())
    return re.sub(r"^[^0-9a-z]+", "", s).strip("-") or s


def _perms(names: Sequence[str], where: str, problems: list[str]) -> int:
    value = 0
    for n in names:
        if n not in PERMISSIONS:
            problems.append(f"{where}: unknown permission {n!r}")
        else:
            value |= PERMISSIONS[n]
    return value


def parse_template(raw: Mapping[str, Any]) -> Template:
    """Validate a parsed template; raises ConfigError listing every problem."""
    problems: list[str] = []
    guild = {}
    for key, value in dict(raw.get("guild") or {}).items():
        field_name, choices = GUILD_FIELDS.get(key, (None, {}))
        if field_name is None or value not in choices:
            problems.append(f"guild.{key}: unknown setting or value")
        else:
            guild[field_name] = choices[value]
    roles = []
    for i, r in enumerate(raw.get("roles") or []):
        where = f"roles[{i}]"
        color = str(r.get("color", "#000000"))
        if not re.fullmatch(r"#?[0-9a-fA-F]{6}", color):
            problems.append(f"{where}.color: expected #RRGGBB")
            color = "000000"
        if r.get("access", "") not in ("", "staff", "holders"):
            problems.append(f"{where}.access: expected \"staff\", \"holders\" or nothing")
        roles.append(RoleSpec(str(r.get("name", "")).strip(), int(color.lstrip("#"), 16), bool(r.get("hoist", False)),
                              bool(r.get("mentionable", False)), _perms(r.get("permissions", []), where, problems),
                              r.get("access", "")))
        if not roles[-1].name:
            problems.append(f"{where}.name: required")
    categories = []
    for i, c in enumerate(raw.get("categories") or []):
        where = f"categories[{i}]"
        if c.get("access") not in PRESETS:
            problems.append(f"{where}.access: expected one of {', '.join(PRESETS)}")
        channels = []
        for j, ch in enumerate(c.get("channels") or []):
            cw = f"{where}.channels[{j}]"
            spec = ChannelSpec(str(ch.get("name", "")).strip(), ch.get("type", "text"), str(ch.get("topic", "")),
                               ch.get("access", ""), tuple(ch.get("roles", ())), _perms(ch.get("bot", []), cw, problems),
                               bool(ch.get("allow_threads", False)))
            if not spec.name:
                problems.append(f"{cw}.name: required")
            if spec.type not in CHANNEL_TYPES:
                problems.append(f"{cw}.type: expected \"text\" or \"voice\"")
            if spec.access and spec.access not in PRESETS:
                problems.append(f"{cw}.access: expected one of {', '.join(PRESETS)}")
            channels.append(spec)
        categories.append(CategorySpec(str(c.get("name", "")).strip(), c.get("access", ""), tuple(channels)))
    cores = [core_name(ch.name) for c in categories for ch in c.channels]
    for dup in sorted({n for n in cores if cores.count(n) > 1}):
        problems.append(f"channels: two channels are both called {dup!r}")
    if problems:
        raise ConfigError(problems)
    return Template(guild, tuple(roles), tuple(categories))


def load_template(path: Path) -> Template:
    try:
        return parse_template(tomllib.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        raise ConfigError([f"{path}: not found"]) from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError([f"{path}: {e}"]) from None


def all_roles(t: Template, s: Settings) -> list[RoleSpec]:
    """Template roles (staff first) plus the roles config.toml needs: specials, tiers, collections, self-roles."""
    roles = list(t.roles)
    seen = {r.name.casefold() for r in roles}
    extra = ([RoleSpec(sp.role, hoist=True) for sp in s.specials]
             + [RoleSpec(tier.name, hoist=True) for tier in reversed(s.tiers)]
             + [RoleSpec(c.role, hoist=True) for c in s.collections if c.role]
             + [RoleSpec(r.name) for r in s.self_roles]
             + ([RoleSpec(s.raffles.alert_role)] if s.raffles.alert_role else []))
    for r in extra:
        if r.name.casefold() not in seen:
            roles.append(r)
            seen.add(r.name.casefold())
    return roles


def overwrites_for(access: str, kind: str, holders: Sequence[str], staff: Sequence[str], bot: int | None,
                   allow_threads: bool = False) -> dict[str, tuple[int, int]]:
    """target (@everyone, @bot or a role name) -> (allow, deny) for an access preset.

    `bot` None means "no overwrite for the bot" (categories); otherwise extra bot permissions.
    """
    see = VIEW | (VOICE if kind == "voice" else 0)
    shut = WRITE & ~P["send_messages_in_threads"] if allow_threads else WRITE
    out: dict[str, tuple[int, int]] = {}
    if access == "public_readonly":
        out[EVERYONE] = (0, shut)
        out.update({r: (WRITE, 0) for r in staff})
    elif access == "holders":
        out[EVERYONE] = (0, see)
        out.update({r: (see, 0) for r in [*holders, *staff]})
    elif access == "holders_readonly":
        out[EVERYONE] = (0, see | shut)
        out.update({r: (see, 0) for r in holders})
        out.update({r: (see | WRITE, 0) for r in staff})
    elif access == "staff":
        out[EVERYONE] = (0, see)
        out.update({r: (see | WRITE, 0) for r in staff})
    if bot is not None and kind == "text" and (access != "public" or bot):
        out[BOT] = (BOT_BASE | bot, 0)
    return out


def plan(t: Template, s: Settings, state: Mapping[str, Any]) -> Plan:
    """Compare the template with a server. `state` is the REST view of the server:

    {"guild": GET /guilds/{id}, "roles": GET .../roles, "channels": GET .../channels,
     "bot_id": the bot user id, "bot_roles": the bot member's role ids}
    """
    actions: list[Action] = []
    notes: list[str] = []
    roles = all_roles(t, s)
    known = {r.name.casefold() for r in roles}
    unknown = sorted({n for c in t.categories for ch in c.channels for n in ch.roles if n.casefold() not in known})
    if unknown:
        raise ConfigError([f"template: channel roles that no template or config role defines: {', '.join(unknown)}"])
    existing_roles = {r["name"].casefold(): r for r in state["roles"]}
    for spec in roles:
        if spec.name.casefold() not in existing_roles:
            actions.append(Action("create_role", spec.name, {"permissions": str(spec.permissions), "color": spec.color,
                                                             "hoist": spec.hoist, "mentionable": spec.mentionable}))
    bot_top = max((r["position"] for r in state["roles"] if str(r["id"]) in set(map(str, state["bot_roles"]))),
                  default=0)
    for name in s.holder_roles() + [r.name for r in s.self_roles] + [s.raffles.alert_role]:
        r = existing_roles.get(name.casefold()) if name else None
        if r and r["position"] >= bot_top:
            notes.append(f"Role {r['name']!r} sits above the bot's role, so the bot cannot give it: "
                         "Server Settings > Roles, drag the bot's role above it.")

    staff = [r.name for r in roles if r.access == "staff"]
    see_holders = [s.tiers[0].name] + [r.name for r in roles if r.access == "holders"]
    ids = {EVERYONE: str(state["guild"]["id"]), BOT: str(state["bot_id"])}
    ids.update({r["name"].casefold(): str(r["id"]) for r in state["roles"]})
    by_core: dict[tuple[int, str], dict] = {}
    for ch in state["channels"]:
        by_core.setdefault((ch["type"], core_name(ch["name"])), ch)

    def missing(over: dict[str, tuple[int, int]], existing: Sequence[Mapping]) -> list[tuple[str, tuple[int, int]]]:
        have = {str(o["id"]) for o in existing}
        return [(target, ad) for target, ad in over.items()
                if ids.get(target if target in (EVERYONE, BOT) else target.casefold()) not in have]

    for cat in t.categories:
        over = overwrites_for(cat.access, "text", see_holders, staff, None)
        found = by_core.get((CATEGORY, core_name(cat.name)))
        if found is None:
            actions.append(Action("create_category", cat.name, {"overwrites": list(over.items())}))
        elif add := missing(over, found.get("permission_overwrites", [])):
            actions.append(Action("add_overwrites", found["name"], {"id": str(found["id"]), "overwrites": add}))
        for ch in cat.channels:
            kind = CHANNEL_TYPES.get(ch.type, 0)
            over = overwrites_for(ch.access or cat.access, ch.type, list(ch.roles) or see_holders, staff, ch.bot,
                                  ch.allow_threads)
            found_ch = by_core.get((kind, core_name(ch.name)))
            if found_ch is None:
                actions.append(Action("create_channel", ch.name, {"type": kind, "type_name": ch.type, "topic": ch.topic,
                                                                  "overwrites": list(over.items())}, cat.name))
                continue
            if found is None or str(found_ch.get("parent_id")) != str(found["id"]):
                notes.append(f"#{found_ch['name']} already exists outside {cat.name!r}; it stays where it is.")
            if add := missing(over, found_ch.get("permission_overwrites", [])):
                actions.append(Action("add_overwrites", found_ch["name"], {"id": str(found_ch["id"]), "overwrites": add}))

    changes = {k: v for k, v in t.guild.items() if state["guild"].get(k) != v}
    if changes:
        actions.append(Action("update_guild", "server", changes))
    return Plan(actions, notes)


def overwrite_payload(target: str, allow_deny: tuple[int, int], ids: Mapping[str, str]) -> dict[str, Any]:
    """REST shape of one overwrite; `ids` maps @everyone, @bot and casefolded role names to ids."""
    key = target if target in (EVERYONE, BOT) else target.casefold()
    return {"id": ids[key], "type": 1 if target == BOT else 0,
            "allow": str(allow_deny[0]), "deny": str(allow_deny[1])}
