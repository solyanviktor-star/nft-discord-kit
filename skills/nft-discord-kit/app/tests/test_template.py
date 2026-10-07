"""The server template and the planner against fake REST state: create only what is missing, never delete."""
from __future__ import annotations

import pytest

from conftest import APP, make_settings
from kit.config import ConfigError
from kit.template import (BOT, EVERYONE, PERMISSIONS as P, WRITE, core_name, load_template, overwrites_for,
                          parse_template, plan)

TEMPLATE = load_template(APP / "templates" / "server.toml")
SETTINGS = make_settings()
ALLOWED = {"create_role", "create_category", "create_channel", "add_overwrites", "update_guild"}


def fresh_state(**over):
    state = {"guild": {"id": "1000", "verification_level": 0, "default_message_notifications": 0,
                       "explicit_content_filter": 0},
             "roles": [{"id": "1000", "name": "@everyone", "position": 0}, {"id": "50", "name": "kit-bot", "position": 5}],
             "channels": [{"id": "60", "name": "Text Channels", "type": 4}, {"id": "61", "name": "general", "type": 0}],
             "bot_id": "1", "bot_roles": ["50"]}
    state.update(over)
    return state


def by_name(p, kind):
    return {a.name: a for a in p.actions if a.kind == kind}


def test_core_names():
    assert core_name("\U0001F381┃giveaways") == core_name("Giveaways") == core_name("gIveaways") == "giveaways"
    assert core_name("\U0001F4CB┃ticket-log") == core_name("ticket log") == "ticket-log"
    assert core_name("\U0001F381") == "\U0001F381"  # nothing but an emoji: kept as is


def test_fresh_server_gets_everything():
    p = plan(TEMPLATE, SETTINGS, fresh_state())
    assert {a.kind for a in p.actions} <= ALLOWED
    roles = [a.name for a in p.actions if a.kind == "create_role"]
    assert roles == ["Team", "Mod", "Raffle Manager", "Collab Manager", "Legendary", "Whale", "Collector", "Holder",
                     "Giveaway Alerts"]
    assert len(by_name(p, "create_category")) == 9 and len(by_name(p, "create_channel")) == 17
    assert p.actions[-1].kind == "update_guild" and p.actions[-1].data == {
        "verification_level": 1, "default_message_notifications": 1, "explicit_content_filter": 2}
    # the order the builder needs: every role before any channel, every category before its channels
    kinds = [a.kind for a in p.actions]
    assert kinds.index("create_category") > max(i for i, k in enumerate(kinds) if k == "create_role")


def test_presets():
    p = plan(TEMPLATE, SETTINGS, fresh_state())
    channels = by_name(p, "create_channel")
    verify = dict(channels["✅┃verify"].data["overwrites"])
    assert verify[EVERYONE] == (0, WRITE) and BOT in verify  # public, read-only
    holders = dict(by_name(p, "create_category")["\U0001F48E Holders"].data["overwrites"])
    assert holders[EVERYONE] == (0, P["view_channel"])
    for role in ("Holder", "Raffle Manager", "Collab Manager", "Team", "Mod"):
        assert holders[role][0] & P["view_channel"]
    giveaways = dict(channels["\U0001F381┃giveaways"].data["overwrites"])
    assert giveaways[BOT][0] & P["mention_everyone"] and giveaways[EVERYONE][1] & P["send_messages"]
    assert giveaways["Team"][0] & P["send_messages"] and not giveaways["Holder"][0] & P["send_messages"]
    tickets = dict(channels["\U0001F4E9┃tickets"].data["overwrites"])
    assert not tickets[EVERYONE][1] & P["send_messages_in_threads"]  # people can write in their ticket thread
    assert tickets[BOT][0] & P["create_private_threads"] and tickets[BOT][0] & P["manage_threads"]
    log = dict(channels["\U0001F4CB┃ticket-log"].data["overwrites"])
    assert "Holder" not in log and log[EVERYONE] == (0, P["view_channel"])
    stage = dict(channels["\U0001F399┃stage"].data["overwrites"])
    assert stage["Holder"][0] & P["connect"] and BOT not in stage
    assert channels["\U0001F399┃stage"].data["type"] == 2


def test_existing_things_are_matched_and_completed_not_replaced():
    roles = fresh_state()["roles"] + [{"id": "70", "name": "holder", "position": 1},  # other case: still a match
                                      {"id": "71", "name": "Team", "position": 9}]
    channels = [{"id": "80", "name": "\U0001F381 Giveaways", "type": 4, "permission_overwrites": []},
                {"id": "81", "name": "giveaways", "type": 0, "parent_id": "80",
                 "permission_overwrites": [{"id": "1000", "type": 0, "allow": "0", "deny": "1024"}]},
                {"id": "82", "name": "verify", "type": 0, "parent_id": "999", "permission_overwrites": []}]
    p = plan(TEMPLATE, SETTINGS, fresh_state(roles=roles, channels=channels))
    assert "Holder" not in by_name(p, "create_role") and "Team" not in by_name(p, "create_role")
    assert "\U0001F381┃giveaways" not in by_name(p, "create_channel")
    add = by_name(p, "add_overwrites")["giveaways"]
    targets = [t for t, _ in add.data["overwrites"]]
    assert EVERYONE not in targets  # it already has one: never touched
    assert "Holder" in targets and BOT in targets and add.data["id"] == "81"
    assert any("verify" in n and "outside" in n for n in p.notes)
    assert all(a.kind in ALLOWED for a in p.actions)


def test_hierarchy_note_for_roles_above_the_bot():
    roles = fresh_state()["roles"] + [{"id": "70", "name": "Whale", "position": 9}]
    p = plan(TEMPLATE, SETTINGS, fresh_state(roles=roles))
    assert any("'Whale' sits above the bot's role" in n for n in p.notes)


def test_template_problems():
    with pytest.raises(ConfigError) as err:
        parse_template({"guild": {"verification_level": "extreme"},
                        "roles": [{"name": "", "color": "red", "permissions": ["fly"]}],
                        "categories": [{"name": "A", "access": "vip", "channels": [
                            {"name": "x", "type": "forum"}, {"name": "\U0001F381 x"}]}]})
    text = "\n".join(err.value.problems)
    for bit in ("guild.verification_level", "color", "unknown permission 'fly'", "name: required", "access",
                "type", "two channels are both called 'x'"):
        assert bit in text
    t = parse_template({"categories": [{"name": "A", "access": "holders",
                                        "channels": [{"name": "lounge", "roles": ["Nobody"]}]}]})
    with pytest.raises(ConfigError):
        plan(t, SETTINGS, fresh_state())


def test_voice_and_public_presets():
    assert overwrites_for("public", "text", ["Holder"], ["Team"], 0) == {}
    assert overwrites_for("public", "text", ["Holder"], ["Team"], P["mention_everyone"])[BOT][0] & P["mention_everyone"]
    voice = overwrites_for("holders", "voice", ["Holder"], [], 0)
    assert voice[EVERYONE] == (0, P["view_channel"] | P["connect"] | P["speak"]) and BOT not in voice
