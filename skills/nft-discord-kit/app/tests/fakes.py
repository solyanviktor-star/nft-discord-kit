"""Light stand-ins for discord.py objects, enough to drive the bot's handlers without a gateway.

They record what the bot sends, so tests can assert on replies, posted messages, threads and roles.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord

from kit.bot.client import KitBot
from kit.service import Kit
from kit.store import Store

_ids = itertools.count(900_000)


def next_id() -> int:
    return next(_ids)


def not_found() -> discord.NotFound:
    return discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), {"code": 10008, "message": "Unknown"})


def forbidden() -> discord.Forbidden:
    return discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"),
                             {"code": 50013, "message": "Missing Permissions"})


@dataclass(eq=False)
class FakeRole:
    id: int
    name: str
    mentionable: bool = False
    members: list[Any] = field(default_factory=list)

    @property
    def mention(self) -> str:
        return f"<@&{self.id}>"


@dataclass(eq=False)
class FakeUser:
    id: int
    name: str
    bot: bool = False

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"

    @property
    def display_name(self) -> str:
        return self.name.title()


@dataclass(eq=False)
class FakeMember(FakeUser):
    roles: list[FakeRole] = field(default_factory=list)
    manage_guild: bool = False
    guild: Any = None
    forbid_roles: bool = False

    @property
    def guild_permissions(self) -> SimpleNamespace:
        return SimpleNamespace(manage_guild=self.manage_guild, administrator=False)

    async def add_roles(self, *roles: FakeRole, reason: str = "") -> None:
        if self.forbid_roles:
            raise forbidden()
        for role in roles:
            if role not in self.roles:
                self.roles.append(role)
                role.members.append(self)

    async def remove_roles(self, *roles: FakeRole, reason: str = "") -> None:
        if self.forbid_roles:
            raise forbidden()
        for role in roles:
            if role in self.roles:
                self.roles.remove(role)
                role.members.remove(self)


@dataclass(eq=False)
class FakeMessage:
    id: int
    channel: Any
    content: str | None = None
    embeds: list[discord.Embed] = field(default_factory=list)
    view: Any = None
    file: Any = None
    author: Any = None
    created_at: datetime = field(default_factory=lambda: datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc))
    attachments: list[Any] = field(default_factory=list)
    edits: list[dict[str, Any]] = field(default_factory=list)


class FakeChannel:
    def __init__(self, guild: FakeGuild, name: str, mention_everyone: bool = True):
        self.id, self.name, self.guild = next_id(), name, guild
        self.mention_everyone = mention_everyone
        self.messages: list[FakeMessage] = []
        self.threads: list[FakeThread] = []
        self.fail_create_thread: Exception | None = None
        self.deleted: set[int] = set()

    @property
    def mention(self) -> str:
        return f"<#{self.id}>"

    def permissions_for(self, member: Any) -> SimpleNamespace:
        return SimpleNamespace(mention_everyone=self.mention_everyone)

    async def send(self, content: str | None = None, *, embed: discord.Embed | None = None, view: Any = None,
                   file: Any = None, allowed_mentions: Any = None) -> FakeMessage:
        msg = FakeMessage(next_id(), self, content, [embed] if embed else [], view, file, self.guild.me)
        msg.allowed_mentions = allowed_mentions
        self.messages.append(msg)
        return msg

    def get_partial_message(self, message_id: int) -> SimpleNamespace:
        async def edit(**kw: Any) -> None:
            if message_id in self.deleted:
                raise not_found()
            target = next(m for m in self.messages if m.id == message_id)
            target.edits.append(kw)

        return SimpleNamespace(edit=edit)

    async def create_thread(self, **kw: Any) -> FakeThread:
        if self.fail_create_thread:
            raise self.fail_create_thread
        thread = FakeThread(self, kw)
        self.threads.append(thread)
        self.guild.channels[thread.id] = thread
        return thread


class FakeThread(FakeChannel):
    def __init__(self, parent: FakeChannel, created_with: dict[str, Any]):
        super().__init__(parent.guild, created_with["name"])
        self.parent, self.created_with = parent, created_with
        self.archived = False
        self.locked = False
        self.added: list[Any] = []
        self.removed: list[Any] = []

    @property
    def jump_url(self) -> str:
        return f"https://discord.com/channels/{self.guild.id}/{self.id}"

    async def add_user(self, user: Any) -> None:
        self.added.append(user)

    async def remove_user(self, user: Any) -> None:
        self.removed.append(user)

    async def edit(self, archived: bool | None = None, locked: bool | None = None) -> None:
        self.archived = bool(archived)
        self.locked = bool(locked)

    async def history(self, limit: int | None = None, oldest_first: bool = True):
        for m in self.messages:
            yield m


class FakeGuild:
    def __init__(self) -> None:
        self.id = 1000
        self.name = "Test Server"
        self.roles: list[FakeRole] = []
        self.members: dict[int, FakeMember] = {}
        self.channels: dict[int, FakeChannel] = {}
        self.me = FakeMember(1, "kit-bot", bot=True)

    def add_role(self, name: str, mentionable: bool = False) -> FakeRole:
        role = FakeRole(next_id(), name, mentionable)
        self.roles.append(role)
        return role

    def add_member(self, user_id: int, name: str, *roles: str, manage_guild: bool = False) -> FakeMember:
        member = FakeMember(user_id, name, guild=self, manage_guild=manage_guild)
        self.members[user_id] = member
        for role_name in roles:
            role = next(r for r in self.roles if r.name == role_name)
            member.roles.append(role)
            role.members.append(member)
        return member

    def add_channel(self, name: str, **kw: Any) -> FakeChannel:
        channel = FakeChannel(self, name, **kw)
        self.channels[channel.id] = channel
        return channel

    @property
    def text_channels(self) -> list[FakeChannel]:
        return [c for c in self.channels.values() if not isinstance(c, FakeThread)]

    def get_channel(self, channel_id: int) -> FakeChannel | None:
        return self.channels.get(int(channel_id))

    def get_role(self, role_id: int) -> FakeRole | None:
        return next((r for r in self.roles if r.id == int(role_id)), None)

    def get_member(self, user_id: int) -> FakeMember | None:
        return self.members.get(int(user_id))

    async def fetch_member(self, user_id: int) -> FakeMember:
        member = self.members.get(int(user_id))
        if member is None:
            raise not_found()
        return member


class FakeResponse:
    def __init__(self, log: list[tuple[str, Any, dict[str, Any]]]):
        self.log, self._done = log, False

    def is_done(self) -> bool:
        return self._done

    async def send_message(self, content: str | None = None, **kw: Any) -> None:
        assert not self._done, "responded twice"
        self._done = True
        self.log.append(("send", content, kw))

    async def defer(self, **kw: Any) -> None:
        assert not self._done, "responded twice"
        self._done = True
        self.log.append(("defer", None, kw))

    async def send_modal(self, modal: Any) -> None:
        assert not self._done, "responded twice"
        self._done = True
        self.log.append(("modal", modal, {}))


class FakeFollowup:
    def __init__(self, log: list[tuple[str, Any, dict[str, Any]]]):
        self.log = log

    async def send(self, content: str | None = None, **kw: Any) -> None:
        self.log.append(("followup", content, kw))


class FakeInteraction:
    def __init__(self, bot: KitBot, user: Any, channel: Any = None):
        self.client, self.user, self.channel = bot, user, channel
        self.guild = bot.guild
        self.log: list[tuple[str, Any, dict[str, Any]]] = []
        self.response = FakeResponse(self.log)
        self.followup = FakeFollowup(self.log)

    @property
    def replies(self) -> list[str]:
        """Text of every message sent back (send_message and followups)."""
        return [c for kind, c, _ in self.log if kind in ("send", "followup") and c]

    @property
    def last(self) -> tuple[str, Any, dict[str, Any]]:
        return self.log[-1]


class FakeBot(KitBot):
    """The real KitBot with its server swapped for a FakeGuild (no gateway, no REST)."""

    def __init__(self, kit: Kit, data_dir: Path, guild: FakeGuild):
        super().__init__(kit, http=None, data_dir=data_dir)  # type: ignore[arg-type]
        self.fake_guild = guild

    @property
    def guild(self) -> FakeGuild:  # type: ignore[override]
        return self.fake_guild

    def get_channel(self, channel_id: int) -> Any:  # type: ignore[override]
        return self.fake_guild.get_channel(channel_id)

    async def fetch_channel(self, channel_id: int) -> Any:  # type: ignore[override]
        found = self.fake_guild.get_channel(channel_id)
        if found is None:
            raise not_found()
        return found

    async def settle(self) -> None:
        """Let background tasks (card redraws) finish."""
        import asyncio
        while self._tasks:
            await asyncio.gather(*list(self._tasks))


@dataclass
class World:
    bot: FakeBot
    kit: Kit
    guild: FakeGuild
    reader: Any
    staff: FakeMember
    holder: FakeMember
    newbie: FakeMember
    channels: dict[str, FakeChannel]

    def role(self, name: str) -> FakeRole:
        return next(r for r in self.guild.roles if r.name == name)

    def interaction(self, user: Any, channel: Any = None) -> FakeInteraction:
        return FakeInteraction(self.bot, user, channel)


def make_world(tmp_path: Path, settings: Any, reader: Any, clock: Any = None, rng: Any = None) -> World:
    guild = FakeGuild()
    for name in ("Team", "Mod", "Raffle Manager", "Whale", "Collector", "Holder", "Legendary", "Giveaway Alerts",
                 "Unrelated"):
        guild.add_role(name)
    channels = {name: guild.add_channel(name) for name in
                ("✅┃verify", "\U0001F381┃giveaways", "\U0001F3C6┃giveaways-winners",
                 "\U0001F4E9┃tickets", "\U0001F4CB┃ticket-log", "\U0001F3AD┃claim-roles")}
    store = Store(tmp_path / "kit.db")
    kwargs = {"clock": clock} if clock else {}
    kit = Kit(settings, store, reader, b"s" * 32, rng=rng, **kwargs)
    bot = FakeBot(kit, tmp_path, guild)
    kit.discord = bot.port
    staff = guild.add_member(2001, "staffer", "Team")
    holder = guild.add_member(3001, "holder", "Holder")
    newbie = guild.add_member(3002, "newbie")
    by_purpose = {"verify": "✅┃verify", "giveaways": "\U0001F381┃giveaways",
                  "winners": "\U0001F3C6┃giveaways-winners", "tickets": "\U0001F4E9┃tickets",
                  "ticket_log": "\U0001F4CB┃ticket-log", "self_roles": "\U0001F3AD┃claim-roles"}
    return World(bot, kit, guild, reader, staff, holder, newbie, {k: channels[v] for k, v in by_purpose.items()})
