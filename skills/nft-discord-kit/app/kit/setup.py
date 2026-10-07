"""Server setup over the Discord REST API (no gateway connection):

  python -m kit.setup invite-url [--admin]   the link that adds the bot to a server
  python -m kit.setup guilds                 servers the bot is in (to find discord.guild_id)
  python -m kit.setup plan                   read-only dry run: what `build` would create (GET requests only)
  python -m kit.setup build [--yes]          create what is missing; never deletes, renames or moves anything
  python -m kit.setup panels                 post or refresh the verify, tickets and self-roles panels

Reads config.toml, templates/server.toml and DISCORD_TOKEN from .env in the current folder.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import aiohttp

from . import panels, statefile, template
from .config import ConfigError, Settings, load_env_file, load_settings
from .rest import DiscordError, Rest
from .template import BOT, CATEGORY, EVERYONE, PERMISSIONS, Plan, Template, core_name, overwrite_payload

P = PERMISSIONS
BOT_PERMISSIONS = (P["view_channel"] | P["send_messages"] | P["send_messages_in_threads"] | P["create_private_threads"]
                   | P["manage_threads"] | P["embed_links"] | P["attach_files"] | P["read_message_history"]
                   | P["mention_everyone"] | P["manage_roles"])
FRESH = {"text-channels", "voice-channels", "general"}  # what Discord's "Create My Own" server starts with
REASON = "nft-discord-kit setup"


def invite_url(application_id: int, s: Settings, admin: bool) -> str:
    perms = P["administrator"] if admin else BOT_PERMISSIONS | (P["create_instant_invite"] if s.auto_join else 0)
    url = (f"https://discord.com/oauth2/authorize?client_id={application_id}&scope=bot+applications.commands"
           f"&permissions={perms}")
    return url + (f"&guild_id={s.discord.guild_id}&disable_guild_select=true" if s.discord.guild_id else "")


async def guild_state(rest: Rest, guild_id: int) -> dict[str, Any]:
    """The server as the planner sees it. GET requests only."""
    me = await rest.request("GET", "/users/@me")
    guild = await rest.request("GET", f"/guilds/{guild_id}")
    channels = await rest.request("GET", f"/guilds/{guild_id}/channels")
    member = await rest.request("GET", f"/guilds/{guild_id}/members/{me['id']}")
    return {"guild": guild, "roles": guild["roles"], "channels": channels, "bot_id": me["id"],
            "bot_roles": member["roles"]}


def print_plan(p: Plan, state: dict[str, Any]) -> None:
    print(f"Server {state['guild']['name']!r}: {len(state['channels'])} channels, {len(state['roles'])} roles now.")
    if not p.actions:
        print("Nothing to create: the server already has everything in the template.")
    for i, a in enumerate(p.actions, 1):
        print(f"{i:3}. {a.describe()}")
    for note in p.notes:
        print(f"Note: {note}")


def remember(path: Path, s: Settings, t: Template, state: dict[str, Any]) -> None:
    """Write the ids of the template's roles and channels to data/state.json (the bot prefers these)."""
    data = statefile.load(path)
    roles = {r["name"].casefold(): str(r["id"]) for r in state["roles"]}
    chans = {(c["type"], core_name(c["name"])): str(c["id"]) for c in state["channels"]}
    data["guild_id"] = str(state["guild"]["id"])
    data["roles"] = {r.name: roles[r.name.casefold()] for r in template.all_roles(t, s) if r.name.casefold() in roles}
    data["categories"] = {c.name: chans[(CATEGORY, core_name(c.name))] for c in t.categories
                          if (CATEGORY, core_name(c.name)) in chans}
    data["channels"] = {core_name(ch.name): chans[key] for c in t.categories for ch in c.channels
                        if (key := (template.CHANNEL_TYPES.get(ch.type, 0), core_name(ch.name))) in chans}
    statefile.save(path, data)


async def execute(rest: Rest, p: Plan, state: dict[str, Any]) -> None:
    """Carry out a plan in order: roles first (overwrites need their ids), then categories and channels."""
    g = str(state["guild"]["id"])
    ids = {EVERYONE: g, BOT: str(state["bot_id"])}
    ids.update({r["name"].casefold(): str(r["id"]) for r in state["roles"]})
    cats = {core_name(c["name"]): str(c["id"]) for c in state["channels"] if c["type"] == CATEGORY}
    for a in p.actions:
        if a.kind == "create_role":
            role = await rest.request("POST", f"/guilds/{g}/roles", {"name": a.name, **a.data}, REASON)
            ids[a.name.casefold()] = str(role["id"])
        elif a.kind in ("create_category", "create_channel"):
            body: dict[str, Any] = {"name": a.name, "type": CATEGORY if a.kind == "create_category" else a.data["type"],
                                    "permission_overwrites": [overwrite_payload(t, ad, ids)
                                                              for t, ad in a.data["overwrites"]]}
            if a.kind == "create_channel":
                body["parent_id"] = cats.get(core_name(a.parent))
                if a.data.get("topic") and a.data["type"] == 0:
                    body["topic"] = a.data["topic"]
            made = await rest.request("POST", f"/guilds/{g}/channels", body, REASON)
            if a.kind == "create_category":
                cats[core_name(a.name)] = str(made["id"])
        elif a.kind == "add_overwrites":
            for target, allow_deny in a.data["overwrites"]:
                o = overwrite_payload(target, allow_deny, ids)
                await rest.request("PUT", f"/channels/{a.data['id']}/permissions/{o['id']}",
                                   {"type": o["type"], "allow": o["allow"], "deny": o["deny"]}, REASON)
        elif a.kind == "update_guild":
            await rest.request("PATCH", f"/guilds/{g}", a.data, REASON)
        print(f"  done: {a.describe()}")


async def cmd_build(rest: Rest, s: Settings, t: Template, yes: bool, data_dir: Path) -> int:
    state = await guild_state(rest, s.discord.guild_id)
    p = template.plan(t, s, state)
    print_plan(p, state)
    extra = [c["name"] for c in state["channels"] if core_name(c["name"]) not in FRESH]
    if p.actions:
        if extra:
            print(f"This server already has {len(extra)} channels of its own. The build only adds what is missing; "
                  "it never deletes, renames or moves anything.")
        if not yes:
            if not sys.stdin.isatty():
                print("Review the plan above, then run again with --yes to apply it.")
                return 1
            if input("Apply these changes? [y/N] ").strip().lower() not in ("y", "yes"):
                print("Nothing changed.")
                return 1
        await execute(rest, p, state)
        state = await guild_state(rest, s.discord.guild_id)
    remember(data_dir / "state.json", s, t, state)
    print(f"Saved the ids to {data_dir / 'state.json'}. Next: python -m kit.setup panels")
    return 0


async def cmd_panels(rest: Rest, s: Settings, data_dir: Path) -> int:
    path = data_dir / "state.json"
    saved = statefile.load(path)
    state = await guild_state(rest, s.discord.guild_id)
    role_ids = {r["name"]: str(r["id"]) for r in state["roles"]}
    texts = [(str(c["id"]), c["name"]) for c in state["channels"] if c["type"] == 0]
    wanted = [("verify", panels.verify_panel(s, role_ids))]
    if s.support.enabled:
        wanted.append(("tickets", panels.tickets_panel(s)))
    if s.self_roles:
        wanted.append(("self_roles", panels.self_roles_panel(s)))
    posted = saved.setdefault("panels", {})
    for purpose, payload in wanted:
        cid = statefile.find_channel_id(s.channels[purpose], saved.get("channels", {}), texts)
        if not cid:
            print(f"  skipped {purpose}: no channel {s.channels[purpose]!r} (set [channels] {purpose} in config.toml)")
            continue
        prev = posted.get(purpose) or {}
        if prev.get("channel") == cid and prev.get("message"):
            try:
                await rest.request("PATCH", f"/channels/{cid}/messages/{prev['message']}", payload)
                print(f"  refreshed the {purpose} panel")
                continue
            except DiscordError as e:
                if e.status != 404:
                    raise
        msg = await rest.request("POST", f"/channels/{cid}/messages", payload)
        posted[purpose] = {"channel": cid, "message": str(msg["id"])}
        print(f"  posted the {purpose} panel")
    statefile.save(path, saved)
    return 0


def explain(e: DiscordError) -> str:
    hints = {10004: "Unknown server: check discord.guild_id (python -m kit.setup guilds).",
             50001: "Missing Access: the bot is not in that server or cannot see that channel. "
                    "Open the invite-url link, or check the channel's permissions.",
             50013: "Missing Permissions: for the first build invite the bot with `invite-url --admin`, or give "
                    "its role Manage Roles and Manage Channels."}
    if e.status == 401:
        return "Discord rejected DISCORD_TOKEN. Reset it (Developer Portal > Bot) and update .env."
    return hints.get(e.code, str(e))


async def dispatch(args: argparse.Namespace, s: Settings) -> int:
    data_dir = Path(os.environ.get("KIT_DATA_DIR") or "data")
    async with aiohttp.ClientSession() as session:
        writes = args.command in ("build", "panels")
        rest = Rest(session, s.discord_token, read_only=not writes)
        if args.command == "invite-url":
            app_id = s.discord.application_id or int((await rest.request("GET", "/applications/@me"))["id"])
            print(invite_url(app_id, s, args.admin))
            return 0
        if args.command == "guilds":
            guilds = await rest.request("GET", "/users/@me/guilds")
            for g in guilds:
                print(f"{g['id']}  {g['name']}")
            if not guilds:
                print("The bot is in no server yet: open the link from `python -m kit.setup invite-url`.")
            return 0
        if args.command == "panels":
            return await cmd_panels(rest, s, data_dir)
        t = template.load_template(Path(args.template))
        if args.command == "plan":
            state = await guild_state(rest, s.discord.guild_id)
            print_plan(template.plan(t, s, state), state)
            return 0
        return await cmd_build(rest, s, t, args.yes, data_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m kit.setup", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=os.environ.get("KIT_CONFIG", "config.toml"), help="default: config.toml")
    parser.add_argument("--template", default="templates/server.toml", help="default: templates/server.toml")
    sub = parser.add_subparsers(dest="command", required=True)
    invite = sub.add_parser("invite-url", help="print the link that adds the bot to a server")
    invite.add_argument("--admin", action="store_true", help="ask for Administrator (simplest for the first build)")
    sub.add_parser("guilds", help="list the servers the bot is in")
    sub.add_parser("plan", help="read-only: show what build would create")
    build = sub.add_parser("build", help="create the missing roles, categories, channels and permissions")
    build.add_argument("--yes", action="store_true", help="apply without asking (after reviewing `plan`)")
    sub.add_parser("panels", help="post or refresh the verify, tickets and self-roles panels")
    args = parser.parse_args(argv)
    load_env_file(Path(".env"))
    try:
        s = load_settings(Path(args.config))
    except ConfigError as e:
        print("Config problems:\n" + "\n".join(f"  - {p}" for p in e.problems), file=sys.stderr)
        return 1
    if not s.discord_token:
        print("DISCORD_TOKEN is not set: put it in .env (never paste it into a chat).", file=sys.stderr)
        return 1
    if args.command in ("plan", "build", "panels") and not s.discord.guild_id:
        print("discord.guild_id is not set: run `python -m kit.setup guilds` and put the id into config.toml.",
              file=sys.stderr)
        return 1
    try:
        return asyncio.run(dispatch(args, s))
    except DiscordError as e:
        print(explain(e), file=sys.stderr)
        return 1
    except ConfigError as e:
        print("Template problems:\n" + "\n".join(f"  - {p}" for p in e.problems), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
