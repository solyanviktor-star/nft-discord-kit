"""Server setup over Discord's API with the bot token (no gateway connection, never a person's account):

  python -m kit.setup token --from-clipboard save the copied bot token into .env (checked, never printed)
  python -m kit.setup app [--force-branding] turn on the bot's intents; set its description and icon
  python -m kit.setup invite-url [--admin]   the link that adds the bot to a server (then runs `app`)
  python -m kit.setup guilds [--wait]        servers the bot is in; --wait waits for the invite and saves
                                             discord.guild_id into config.toml (--pick ID chooses one)
  python -m kit.setup plan                   read-only dry run: what `build` would create (GET requests only)
  python -m kit.setup build [--yes]          create what is missing; never deletes, renames or moves anything
  python -m kit.setup panels                 post or refresh the verify, tickets and self-roles panels

Reads config.toml, templates/server.toml and DISCORD_TOKEN from .env in the current folder.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
import tomllib
from pathlib import Path
from typing import Any

import aiohttp

from . import application, panels, statefile, template
from .config import ConfigError, Settings, load_env_file, load_settings, set_env_value, set_guild_id
from .rest import DiscordError, Rest
from .template import (BOT, CATEGORY, EVERYONE, PERMISSIONS, Plan, Template, core_name, overwrite_payload,
                       permission_names, runtime_permissions)

FRESH = {"text-channels", "voice-channels", "general"}  # what Discord's "Create My Own" server starts with
REASON = "nft-discord-kit setup"
POLL_SECONDS = 5
TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{20,}$")  # id.timestamp.hmac


class ClipboardError(Exception):
    pass


def read_clipboard() -> str:
    """The clipboard's text through tkinter (stdlib); ClipboardError says why it cannot be read."""
    try:
        import tkinter
    except ImportError:
        raise ClipboardError("Python's tkinter is missing (Debian/Ubuntu: apt install python3-tk)") from None
    try:
        root = tkinter.Tk()
    except tkinter.TclError:
        raise ClipboardError("there is no display to read a clipboard from (a server without a desktop?)") from None
    try:
        root.withdraw()
        return str(root.clipboard_get())
    except tkinter.TclError:
        raise ClipboardError("the clipboard holds no text") from None
    finally:
        root.destroy()


def write_env_value(path: Path, key: str, value: str) -> None:
    """Create or replace one line of .env; a new file is readable by its owner only."""
    if path.exists():
        raw = path.read_bytes().decode("utf-8")
        text = set_env_value(raw.replace("\r\n", "\n"), key, value)
        path.write_bytes((text.replace("\n", "\r\n") if "\r\n" in raw else text).encode("utf-8"))
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(set_env_value("", key, value))


async def cmd_token(env_path: Path) -> int:
    """Save the bot token from the clipboard into .env after Discord accepts it. The token is never printed,
    so it never passes through a chat or an agent's context."""
    try:
        token = read_clipboard().strip()
    except ClipboardError as e:
        print(f"Could not read the clipboard: {e}. Paste the token into {env_path} as DISCORD_TOKEN= by hand.",
              file=sys.stderr)
        return 1
    if not TOKEN_SHAPE.match(token):
        print("The clipboard does not hold a bot token. Developer Portal > your application > Bot > Reset Token > "
              "Copy, then run this again.", file=sys.stderr)
        return 1
    async with aiohttp.ClientSession() as session:
        try:
            app = await Rest(session, token, read_only=True).request("GET", "/applications/@me")
        except DiscordError as e:
            print(f"Discord did not accept the token in the clipboard (HTTP {e.status}). Reset it again, press Copy, "
                  "then run this again. Nothing was saved.", file=sys.stderr)
            return 1
    write_env_value(env_path, "DISCORD_TOKEN", token)
    print(f"Saved the bot token of the application {app.get('name')!r} in {env_path}.")
    return 0


def invite_permissions(s: Settings, admin: bool) -> int:
    """Administrator for the first build; otherwise exactly what the running bot needs."""
    return PERMISSIONS["administrator"] if admin else runtime_permissions(s)


def invite_url(application_id: int, s: Settings, admin: bool) -> str:
    url = (f"https://discord.com/oauth2/authorize?client_id={application_id}&scope=bot+applications.commands"
           f"&permissions={invite_permissions(s, admin)}")
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
        if p.build_lack:  # stop before the first change instead of failing halfway with 50013
            print(f"Nothing changed: the bot lacks {', '.join(permission_names(p.build_lack))}. Invite it with "
                  "`python -m kit.setup invite-url --admin` for the build (see references/discord-app.md).")
            return 1
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
             50013: "Missing Permissions: for the build invite the bot with `invite-url --admin`, or give its role "
                    "Manage Roles, Manage Channels and Manage Server plus every permission the template hands out "
                    "(`python -m kit.setup plan` lists what is missing)."}
    if e.status == 401:
        return "Discord rejected DISCORD_TOKEN. Reset it (Developer Portal > Bot) and update .env."
    return hints.get(e.code, str(e))


def save_guild(config: Path, guild: dict[str, Any]) -> int:
    """Write discord.guild_id into config.toml in place (comments and line endings kept)."""
    raw = config.read_bytes().decode("utf-8")
    text = set_guild_id(raw.replace("\r\n", "\n"), int(guild["id"]))
    if tomllib.loads(text).get("discord", {}).get("guild_id") != int(guild["id"]):
        raise ConfigError([f"{config}: could not set discord.guild_id; put {guild['id']} there by hand"])
    config.write_bytes((text.replace("\n", "\r\n") if "\r\n" in raw else text).encode("utf-8"))
    print(f"Server {guild['name']!r} ({guild['id']}): saved as discord.guild_id in {config}.")
    return 0


async def cmd_guilds(rest: Rest, s: Settings, args: argparse.Namespace) -> int:
    """List the bot's servers. --wait polls until the invite went through and saves the server's id;
    --pick saves a given one when the bot is in several."""
    deadline = time.monotonic() + args.timeout
    if args.wait and not args.pick:
        print(f"Waiting up to {args.timeout} s for the bot to join a server (open the invite link and authorize)...")
    while True:
        guilds = await rest.request("GET", "/users/@me/guilds")
        if args.pick:
            chosen = next((g for g in guilds if str(g["id"]) == str(args.pick)), None)
            if chosen is None:
                print(f"The bot is not in server {args.pick}; it is in: "
                      + (", ".join(f"{g['name']} ({g['id']})" for g in guilds) or "no server"))
                return 1
            return save_guild(Path(args.config), chosen)
        if not args.wait:
            for g in guilds:
                print(f"{g['id']}  {g['name']}")
            if not guilds:
                print("The bot is in no server yet: open the link from `python -m kit.setup invite-url`.")
            return 0
        if len(guilds) == 1:
            return save_guild(Path(args.config), guilds[0])
        if len(guilds) > 1:
            current = next((g for g in guilds if str(g["id"]) == str(s.discord.guild_id)), None)
            if current:
                print(f"The bot is in {len(guilds)} servers; config.toml already names {current['name']!r}.")
                return 0
            print(f"The bot is in {len(guilds)} servers:")
            for g in guilds:
                print(f"  {g['id']}  {g['name']}")
            print("Ask which one is the project's server, then: python -m kit.setup guilds --pick <id>")
            return 1
        if time.monotonic() >= deadline:
            print(f"No server after {args.timeout} s. Open the link from `python -m kit.setup invite-url`, pick the "
                  "server, authorize, then run this again.")
            return 1
        await asyncio.sleep(POLL_SECONDS)


async def dispatch(args: argparse.Namespace, s: Settings) -> int:
    data_dir = Path(os.environ.get("KIT_DATA_DIR") or "data")
    async with aiohttp.ClientSession() as session:
        writes = args.command in ("build", "panels", "app", "invite-url")
        rest = Rest(session, s.discord_token, read_only=not writes)
        if args.command in ("app", "invite-url"):
            if args.command == "invite-url":
                app_id = s.discord.application_id or int((await rest.request("GET", "/applications/@me"))["id"])
                print(invite_url(app_id, s, args.admin))
                print("  asks for: " + ", ".join(permission_names(invite_permissions(s, args.admin))))
            lines, ok = await application.configure(rest, s, session, getattr(args, "force_branding", False))
            print("\n".join(lines))
            return 0 if ok or args.command == "invite-url" else 1
        if args.command == "guilds":
            return await cmd_guilds(rest, s, args)
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
    token = sub.add_parser("token", help="save the bot token from the clipboard into .env (checked, never printed)")
    token.add_argument("--from-clipboard", action="store_true", required=True,
                       help="read it from the clipboard after pressing Copy under Reset Token")
    token.add_argument("--env", default=".env", help="default: .env")
    app = sub.add_parser("app", help="turn on the bot's intents; set its description and icon when they are empty")
    app.add_argument("--force-branding", action="store_true", help="also replace an existing description and icon")
    invite = sub.add_parser("invite-url", help="print the link that adds the bot to a server, then run `app`")
    invite.add_argument("--admin", action="store_true",
                        help="ask for Administrator (for the build); without it: only what the running bot needs")
    guilds = sub.add_parser("guilds", help="list the servers the bot is in")
    guilds.add_argument("--wait", action="store_true",
                        help="wait until the bot is in a server, then save its id into config.toml")
    guilds.add_argument("--timeout", type=int, default=600, help="seconds to wait (default 600)")
    guilds.add_argument("--pick", metavar="ID", help="save this server's id (when the bot is in several)")
    sub.add_parser("plan", help="read-only: show what build would create")
    build = sub.add_parser("build", help="create the missing roles, categories, channels and permissions")
    build.add_argument("--yes", action="store_true", help="apply without asking (after reviewing `plan`)")
    sub.add_parser("panels", help="post or refresh the verify, tickets and self-roles panels")
    args = parser.parse_args(argv)
    if args.command == "token":  # before config.toml and the token exist
        return asyncio.run(cmd_token(Path(args.env)))
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
        print("discord.guild_id is not set: run `python -m kit.setup guilds --wait` (it saves the id into "
              "config.toml).", file=sys.stderr)
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
