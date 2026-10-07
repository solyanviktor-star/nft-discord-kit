"""The bot's application settings over the API (intents, description, icon), `guilds --wait`, the clipboard
token command, and the intents line of `kit check`, against the fake Discord API in restfake.py."""
from __future__ import annotations

import base64
import tomllib
from dataclasses import replace

import pytest

from conftest import APP, make_settings
from kit import application, setup
from kit.__main__ import online_checks
from kit.application import (CONTENT, CONTENT_LIMITED, EDITABLE_FLAGS, MEMBERS, MEMBERS_LIMITED, PRESENCE_LIMITED,
                             icon_data_uri, intent_states, merge_intent_flags)
from kit.config import set_env_value, set_guild_id
from restfake import PNG, args

BADGE = 1 << 23  # APPLICATION_COMMAND_BADGE: a flag the API does not let apps change
FAKE_TOKEN = "A" * 24 + ".Bcdefg." + "h" * 27  # the shape of a bot token, not a real one


# --- intents -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("current, sent, added", [
    (0, MEMBERS_LIMITED | CONTENT_LIMITED, ["Server Members", "Message Content"]),
    (MEMBERS_LIMITED, MEMBERS_LIMITED | CONTENT_LIMITED, ["Message Content"]),
    (CONTENT_LIMITED, MEMBERS_LIMITED | CONTENT_LIMITED, ["Server Members"]),
    (MEMBERS, CONTENT_LIMITED, ["Message Content"]),  # the approved full flag is never sent back
    (MEMBERS | CONTENT, 0, []),
    (MEMBERS_LIMITED | CONTENT_LIMITED, MEMBERS_LIMITED | CONTENT_LIMITED, []),
    (PRESENCE_LIMITED | BADGE, PRESENCE_LIMITED | MEMBERS_LIMITED | CONTENT_LIMITED, ["Server Members", "Message Content"]),
])
def test_merge_intent_flags(current, sent, added):
    new, turned_on = merge_intent_flags(current)
    assert (new, turned_on) == (sent, added)
    assert not new & ~EDITABLE_FLAGS  # only bits the API accepts


def test_intent_states():
    assert intent_states(0) == {"Server Members": "off", "Message Content": "off"}
    assert intent_states(MEMBERS | CONTENT_LIMITED) == {"Server Members": "on", "Message Content": "limited"}


# --- icons -------------------------------------------------------------------------------------------------
def test_icon_checks():
    assert icon_data_uri(PNG) == "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert icon_data_uri(b"\xff\xd8\xff\xe0rest").startswith("data:image/jpeg;base64,")
    assert icon_data_uri(b"GIF89a....").startswith("data:image/gif;base64,")
    with pytest.raises(ValueError, match="not a PNG, JPEG or GIF"):
        icon_data_uri(b"<html>no</html>")
    with pytest.raises(ValueError, match="larger than 4 MB"):
        icon_data_uri(PNG + bytes(application.MAX_ICON_BYTES))


# --- kit.setup app -----------------------------------------------------------------------------------------
def with_logo(url: str):
    s = make_settings()
    return replace(s, project=replace(s.project, logo_url=url))


def patches(api, path="/applications/@me"):
    return [body for method, p, body in api.requests if method == "PATCH" and p == path]


async def test_app_turns_on_intents_and_fills_empty_branding(discord_api, capsys):
    discord_api.application.update(flags=PRESENCE_LIMITED | BADGE, description="", icon=None)
    assert await setup.dispatch(args("app"), with_logo(discord_api.url + "/logo.png")) == 0
    sent = patches(discord_api)
    assert sent[0] == {"flags": PRESENCE_LIMITED | MEMBERS_LIMITED | CONTENT_LIMITED}
    assert sent[1]["description"] == "Test Project holder verification, raffles and support"
    assert sent[1]["icon"] == "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert discord_api.application["flags"] & BADGE  # untouched
    out = capsys.readouterr().out
    assert "intents turned on: Server Members, Message Content" in out and "icon set from project.logo_url" in out
    assert "test-token" not in out


async def test_app_keeps_existing_branding_unless_forced(discord_api, capsys):
    discord_api.application.update(flags=MEMBERS | CONTENT, description="Theirs", icon="theirs")
    await setup.dispatch(args("app"), with_logo(discord_api.url + "/logo.png"))
    assert patches(discord_api) == [] and "existing description and icon kept" in capsys.readouterr().out
    await setup.dispatch(args("app", force_branding=True), with_logo(discord_api.url + "/logo.png"))
    assert set(patches(discord_api)[0]) == {"description", "icon"}


async def test_app_prints_the_manual_step_when_discord_refuses(discord_api, capsys):
    discord_api.application.update(flags=0, description="")
    discord_api.refuse_flags = True
    assert await setup.dispatch(args("app"), make_settings()) == 1
    out = capsys.readouterr().out
    assert "Do it by hand: Developer Portal" in out
    assert "Bot > Privileged Gateway Intents: turn on SERVER MEMBERS INTENT and MESSAGE CONTENT INTENT" in out
    assert discord_api.application["description"]  # the description still went through


@pytest.mark.parametrize("path, why", [("/big.png", "larger than 4 MB"), ("/page.html", "not a PNG"),
                                       ("/missing.png", "answered HTTP 404")])
async def test_app_explains_a_logo_it_cannot_use(discord_api, capsys, path, why):
    discord_api.application.update(icon=None, description="")
    await setup.dispatch(args("app"), with_logo(discord_api.url + path))
    out = capsys.readouterr().out
    assert "icon not set: the logo" in out and why in out
    assert patches(discord_api)[-1] == {"description": "Test Project holder verification, raffles and support"}


async def test_invite_url_runs_app(discord_api, capsys):
    discord_api.application.update(flags=0)
    assert await setup.dispatch(args("invite-url", admin=True), make_settings()) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("https://discord.com/oauth2/authorize") and "intents turned on" in out


# --- discord.guild_id in config.toml ------------------------------------------------------------------------
def test_set_guild_id_edits_in_place():
    example = (APP / "config.example.toml").read_text(encoding="utf-8")
    edited = set_guild_id(example, 1000)
    assert tomllib.loads(edited)["discord"]["guild_id"] == 1000
    assert "guild_id = 1000 " in edited and "# your server id: `python -m kit.setup guilds --wait` fills" in edited
    assert len(edited.splitlines()) == len(example.splitlines())  # one line changed, nothing added or lost
    assert [a for a, b in zip(example.splitlines(), edited.splitlines(), strict=True) if a != b] == [
        line for line in example.splitlines() if line.startswith("guild_id")]


@pytest.mark.parametrize("text, expected_lines", [
    ('[discord]\nguild_id = "42"  # quoted\n', ['[discord]', 'guild_id = 7  # quoted', '']),
    ('[discord]\nstaff_roles = ["Team"]\n', ['[discord]', 'guild_id = 7', 'staff_roles = ["Team"]', '']),
    ('[project]\nname = "X"\n', ['[project]', 'name = "X"', '', '[discord]', 'guild_id = 7', '']),
    ('[other]\nguild_id = 1\n[[collections]]\nguild_id = 2\n[discord]\n',
     ['[other]', 'guild_id = 1', '[[collections]]', 'guild_id = 2', '[discord]', 'guild_id = 7', '']),
])
def test_set_guild_id_variants(text, expected_lines):
    assert set_guild_id(text, 7).split("\n") == expected_lines


# --- kit.setup guilds --wait ---------------------------------------------------------------------------------
@pytest.fixture
def config_file(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "POLL_SECONDS", 0)
    path = tmp_path / "config.toml"
    path.write_text((APP / "config.example.toml").read_text(encoding="utf-8"), encoding="utf-8")
    return path


async def test_guilds_wait_picks_up_the_one_server(discord_api, config_file, capsys):
    discord_api.empty_polls = 2  # the person is still clicking through the invite
    assert await setup.dispatch(args("guilds", wait=True, config=str(config_file)), make_settings()) == 0
    assert tomllib.loads(config_file.read_text(encoding="utf-8"))["discord"]["guild_id"] == 1000
    assert "Server 'Fresh Server' (1000): saved as discord.guild_id" in capsys.readouterr().out
    assert [p for _, p, _ in discord_api.requests].count("/users/@me/guilds") == 3


async def test_guilds_wait_with_several_servers_asks_which(discord_api, config_file, capsys):
    discord_api.servers = [{"id": "1000", "name": "Fresh Server"}, {"id": "2000", "name": "Old Server"}]
    nothing_yet = make_settings(discord={"guild_id": 0})
    assert await setup.dispatch(args("guilds", wait=True, config=str(config_file)), nothing_yet) == 1
    out = capsys.readouterr().out
    assert "  2000  Old Server" in out and "guilds --pick <id>" in out
    assert tomllib.loads(config_file.read_text(encoding="utf-8"))["discord"]["guild_id"] == 0
    assert await setup.dispatch(args("guilds", pick="2000", config=str(config_file)), nothing_yet) == 0
    assert tomllib.loads(config_file.read_text(encoding="utf-8"))["discord"]["guild_id"] == 2000
    assert await setup.dispatch(args("guilds", pick="3000", config=str(config_file)), nothing_yet) == 1


async def test_guilds_wait_times_out(discord_api, config_file, capsys):
    discord_api.servers = []
    assert await setup.dispatch(args("guilds", wait=True, timeout=0, config=str(config_file)), make_settings()) == 1
    assert "No server after 0 s" in capsys.readouterr().out


# --- kit.setup token --from-clipboard ------------------------------------------------------------------------
async def test_token_from_clipboard_saves_one_line(discord_api, tmp_path, monkeypatch, capsys):
    env = tmp_path / ".env"
    env.write_text("# keep me\nDISCORD_TOKEN=\nRPC_ETHEREUM=https://rpc.example.com\n", encoding="utf-8")
    discord_api.valid_tokens = {FAKE_TOKEN}
    monkeypatch.setattr(setup, "read_clipboard", lambda: f"  {FAKE_TOKEN}\n")
    assert await setup.cmd_token(env) == 0
    assert env.read_text(encoding="utf-8") == f"# keep me\nDISCORD_TOKEN={FAKE_TOKEN}\nRPC_ETHEREUM=https://rpc.example.com\n"
    out = capsys.readouterr()
    assert "application 'Kit Test'" in out.out and FAKE_TOKEN not in out.out + out.err


@pytest.mark.parametrize("clipboard, message", [("hello there", "does not hold a bot token"),
                                                 ("Z" * 24 + ".Bcdefg." + "z" * 27, "did not accept the token")])
async def test_token_from_clipboard_refuses(discord_api, tmp_path, monkeypatch, capsys, clipboard, message):
    discord_api.valid_tokens = {FAKE_TOKEN}
    monkeypatch.setattr(setup, "read_clipboard", lambda: clipboard)
    assert await setup.cmd_token(tmp_path / ".env") == 1
    assert message in capsys.readouterr().err and not (tmp_path / ".env").exists()


def test_clipboard_without_a_display(monkeypatch):
    tkinter = pytest.importorskip("tkinter")

    def no_display():
        raise tkinter.TclError("no display name and no $DISPLAY environment variable")

    monkeypatch.setattr(tkinter, "Tk", no_display)
    with pytest.raises(setup.ClipboardError, match="no display"):
        setup.read_clipboard()


def test_set_env_value():
    assert set_env_value("", "DISCORD_TOKEN", "x") == "DISCORD_TOKEN=x\n"
    assert set_env_value("A=1\nexport DISCORD_TOKEN=old\n", "DISCORD_TOKEN", "x") == "A=1\nDISCORD_TOKEN=x\n"
    assert set_env_value("A=1", "B", "2") == "A=1\nB=2\n"


# --- kit check -----------------------------------------------------------------------------------------------
async def test_check_reports_the_intents(discord_api):
    lines = await online_checks(make_settings())
    assert lines[0] == "Intents:     Server Members on (limited), Message Content on (limited)"
    discord_api.application["flags"] = MEMBERS
    lines = await online_checks(make_settings())
    assert lines[0] == ("Intents:     Server Members on, Message Content off — `python -m kit.setup app` turns "
                        "them on")
