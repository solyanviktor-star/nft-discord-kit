"""`python -m kit.setup` against a fake Discord REST API: plan is read-only, build only creates, panels refresh."""
from __future__ import annotations

import json
import subprocess
import sys

import aiohttp
import pytest
from aiohttp import web

from conftest import APP, make_settings
from kit import rest, setup, template
from restfake import args


async def test_plan_only_reads(discord_api, capsys):
    assert await setup.dispatch(args("plan"), make_settings()) == 0
    out = capsys.readouterr().out
    assert {m for m, _, _ in discord_api.requests} == {"GET"}
    assert "create role      Team" in out and "create category  ✅ Verification" in out
    assert "create text      \U0001F381┃giveaways  (in \U0001F381 Giveaways)" in out
    assert "update server" in out


async def test_read_only_client_refuses_writes(discord_api):
    async with aiohttp.ClientSession() as session:
        client = rest.Rest(session, "token", read_only=True)
        with pytest.raises(PermissionError):
            await client.request("POST", "/guilds/1000/roles", {"name": "x"})
    assert discord_api.requests == []  # refused before reaching the network


async def test_build_creates_only_what_is_missing(discord_api, capsys, tmp_path):
    s = make_settings()
    assert await setup.dispatch(args("build", yes=True), s) == 0
    methods = [m for m, _, _ in discord_api.requests]
    assert "DELETE" not in methods
    created_roles = [b["name"] for m, p, b in discord_api.requests if m == "POST" and p.endswith("/roles")]
    assert created_roles[:4] == ["Team", "Mod", "Raffle Manager", "Collab Manager"]
    assert created_roles[4:] == ["Legendary", "Whale", "Collector", "Holder", "Giveaway Alerts"]
    first_channel = next(i for i, (m, p, _) in enumerate(discord_api.requests) if p.endswith("/channels") and m == "POST")
    last_role = max(i for i, (m, p, _) in enumerate(discord_api.requests) if p.endswith("/roles") and m == "POST")
    assert last_role < first_channel  # overwrites need the role ids
    verify = next(c for c in discord_api.channels if c["name"] == "✅┃verify")
    category = next(c for c in discord_api.channels if c["name"] == "✅ Verification")
    assert verify["parent_id"] == category["id"]
    everyone = next(o for o in verify["permission_overwrites"] if o["id"] == "1000")
    assert int(everyone["deny"]) & (1 << 11) and not int(everyone["deny"]) & (1 << 10)  # read-only, still visible
    bot = next(o for o in verify["permission_overwrites"] if o["id"] == "1")
    assert bot["type"] == 1 and int(bot["allow"]) & (1 << 14)
    assert all(not str(o["id"]).startswith("@") for c in discord_api.channels for o in c.get("permission_overwrites", []))
    assert discord_api.guild["verification_level"] == 1 and discord_api.guild["default_message_notifications"] == 1
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["channels"]["giveaways"] and saved["roles"]["Holder"]
    assert {c["name"] for c in discord_api.channels} >= {"general", "General"}  # nothing of theirs removed
    discord_api.requests.clear()
    capsys.readouterr()
    assert await setup.dispatch(args("plan"), s) == 0
    assert "Nothing to create" in capsys.readouterr().out


async def test_panels_post_then_refresh(discord_api, capsys):
    s = make_settings()
    await setup.dispatch(args("build", yes=True), s)
    discord_api.requests.clear()
    assert await setup.dispatch(args("panels"), s) == 0
    posts = [(p, b) for m, p, b in discord_api.requests if m == "POST" and p.endswith("/messages")]
    assert len(posts) == 3
    verify_panel = posts[0][1]
    assert verify_panel["embeds"][0]["title"] == "Verify your Test Project holdings"
    assert [b["custom_id"] for b in verify_panel["components"][0]["components"]] == ["kit:verify", "kit:wallets"]
    assert "<@&" in verify_panel["embeds"][0]["description"]  # roles shown as mentions once they exist
    discord_api.requests.clear()
    assert await setup.dispatch(args("panels"), s) == 0
    assert [m for m, p, _ in discord_api.requests if "/messages" in p] == ["PATCH", "PATCH", "PATCH"]


async def test_invite_url_and_guilds(discord_api, capsys):
    s = make_settings()
    await setup.dispatch(args("invite-url"), s)
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("https://discord.com/oauth2/authorize?client_id=1234&scope=bot+applications.commands")
    assert f"permissions={template.RUNTIME_PERMISSIONS}&" in lines[0]
    assert lines[1].startswith("  asks for: View Channels, Send Messages, ") and "Manage Roles" in lines[1]
    assert lines[2] == "Application 'Kit Test' (id 1234):" and lines[3].startswith("  intents already on")
    await setup.dispatch(args("invite-url", admin=True), s)
    lines = capsys.readouterr().out.splitlines()
    assert "permissions=8&" in lines[0] and lines[1] == "  asks for: Administrator"
    await setup.dispatch(args("guilds"), s)
    assert capsys.readouterr().out.splitlines() == ["1000  Fresh Server"]
    assert {m for m, _, _ in discord_api.requests} == {"GET"}  # intents on and branding set: nothing to change
    joining = make_settings(env={"DISCORD_CLIENT_SECRET": "x"}, oauth={"auto_join": True})
    assert setup.invite_permissions(joining, admin=False) & template.P["create_instant_invite"]


async def test_build_refuses_up_front_without_the_permissions(discord_api, capsys):
    bot_role = next(r for r in discord_api.guild["roles"] if r["id"] == "50")
    bot_role["permissions"] = str(template.RUNTIME_PERMISSIONS)  # the runtime set is not enough to build
    assert await setup.dispatch(args("build", yes=True), make_settings()) == 1
    out = capsys.readouterr().out
    assert "Nothing changed: the bot lacks" in out and "Manage Channels" in out and "Manage Server" in out
    assert "Administrator" in out  # the template's Team role carries it, and a bot only hands out what it has
    assert {m for m, _, _ in discord_api.requests} == {"GET"}


async def test_plan_warns_when_the_bot_cannot_manage_roles(discord_api, capsys):
    bot_role = next(r for r in discord_api.guild["roles"] if r["id"] == "50")
    bot_role["permissions"] = str(template.RUNTIME_PERMISSIONS & ~template.P["manage_roles"])  # "Administrator unticked"
    await setup.dispatch(args("plan"), make_settings())
    out = capsys.readouterr().out
    assert "Note: The bot's role lacks Manage Roles: holder roles, self-roles" in out


def test_docs_name_exactly_the_permissions_invite_url_asks_for():
    for doc in (APP.parent / "SKILL.md", APP.parent / "references" / "discord-app.md"):
        text = " ".join(doc.read_text(encoding="utf-8").split())
        for name in template.permission_names(template.RUNTIME_PERMISSIONS):
            assert name in text, (doc.name, name)
        assert "Create Invite" in text and "MESSAGE CONTENT INTENT" in text


def test_missing_permissions_hint_names_manage_server():
    hint = setup.explain(rest.DiscordError(403, 50013, "Missing Permissions"))
    assert "Manage Server" in hint and "Manage Roles" in hint and "Manage Channels" in hint


class FlakyDiscord:
    """Answers with a proxy's HTML pages first, then JSON."""

    def __init__(self, pages):
        self.pages, self.hits = list(pages), 0

    async def handle(self, request):
        self.hits += 1
        status, body, headers = self.pages.pop(0) if self.pages else (200, '{"id": "1"}', {})
        return web.Response(status=status, text=body, headers=headers, content_type="text/html")


@pytest.mark.parametrize("pages, result", [
    ([(429, "<html>slow down</html>", {"Retry-After": "0"})], {"id": "1"}),
    ([(502, "<html>bad gateway</html>", {})] * 3, "HTTP 502"),
    ([(400, "<html>nope</html>", {})], "nope"),
])
async def test_rest_survives_non_json_answers(aiohttp_server, monkeypatch, pages, result):
    fake = FlakyDiscord(pages)
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", fake.handle)
    server = await aiohttp_server(app)
    monkeypatch.setattr(rest, "API", str(server.make_url("")).rstrip("/"))
    monkeypatch.setattr(rest, "SERVER_ERROR_WAIT", 0)
    async with aiohttp.ClientSession() as session:
        client = rest.Rest(session, "token")
        if isinstance(result, dict):
            assert await client.request("GET", "/users/@me") == result and fake.hits == 2
        else:
            with pytest.raises(rest.DiscordError, match=result):
                await client.request("GET", "/users/@me")


def test_setup_help_runs():
    out = subprocess.run([sys.executable, "-m", "kit.setup", "--help"], cwd=APP, capture_output=True, text=True,
                         timeout=60)
    assert out.returncode == 0 and "invite-url" in out.stdout and "read-only" in out.stdout
