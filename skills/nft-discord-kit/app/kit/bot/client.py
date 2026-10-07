"""The Discord client: one server, persistent buttons, slash commands and a 30-second ticker.

`GuildPort` is how the core (`kit.service.Kit`) reaches Discord: members, roles, adding a member after
the website login, and the throttled raffle-card redraw.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import aiohttp
import discord
from discord import app_commands

from .. import statefile
from ..rest import DiscordError, Rest
from ..service import Kit, MemberInfo
from . import commands, raffles, support, verify
from .common import member_info

log = logging.getLogger("kit.bot")
TICK = 30  # seconds


class GuildPort:
    """`kit.service.DiscordPort` for the configured server."""

    def __init__(self, bot: KitBot):
        self.bot = bot

    async def member(self, user_id: str) -> Any | None:
        guild = self.bot.guild
        if guild is None or not str(user_id).isdigit():
            return None
        found = guild.get_member(int(user_id))
        if found is not None:
            return found
        try:
            return await guild.fetch_member(int(user_id))
        except discord.NotFound:
            return None
        except discord.HTTPException as e:
            log.warning("fetch_member %s: %s", user_id, e)
            return None

    async def get_member(self, user_id: str) -> MemberInfo | None:
        found = await self.member(user_id)
        return member_info(found) if found else None

    async def edit_roles(self, user_id: str, add: set[str], remove: set[str], reason: str) -> list[str]:
        """Only the roles named here move; every other role of the member stays as it is."""
        found = await self.member(user_id)
        if found is None:
            return ["not in the server"]
        problems: list[str] = []

        def resolve(names: set[str]) -> list[Any]:
            out = []
            for name in sorted(names):
                role = self.bot.role(name)
                if role is None:
                    problems.append(f"the role {name!r} does not exist on the server")
                else:
                    out.append(role)
            return out

        try:
            if roles := resolve(add):
                await found.add_roles(*roles, reason=reason)
            if roles := resolve(remove):
                await found.remove_roles(*roles, reason=reason)
        except discord.Forbidden:
            log.warning("no permission to edit roles for %s", user_id)
            problems.append("the bot may not manage these roles (its own role must sit above them)")
        except discord.HTTPException as e:
            problems.append(f"Discord error {e.status}")
        log.info("roles %s: +%s -%s (%s)%s", found.name, sorted(add), sorted(remove), reason,
                 f" problems: {problems}" if problems else "")
        return problems

    async def add_member(self, user_id: str, access_token: str, roles: set[str]) -> bool:
        role_ids = [str(r.id) for r in (self.bot.role(n) for n in roles) if r is not None]
        try:
            await self.bot.rest.request("PUT", f"/guilds/{self.bot.s.discord.guild_id}/members/{user_id}",
                                        {"access_token": access_token, "roles": role_ids},
                                        reason="joined through the verification website")
            return True
        except DiscordError as e:
            log.warning("could not add %s to the server: %s", user_id, e)
            return False

    def raffle_changed(self, raffle_id: str) -> None:
        self.bot.spawn(self.bot.cards.refresh(self.bot.kit.store.raffle(raffle_id)))


class KitBot(discord.Client):
    def __init__(self, kit: Kit, http: aiohttp.ClientSession, data_dir: Path):
        intents = discord.Intents.default()
        intents.members = True  # privileged: turn on "Server Members Intent" in the Developer Portal
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.kit, self.s, self.data_dir = kit, kit.s, data_dir
        self.tree = app_commands.CommandTree(self)
        self.rest = Rest(http, kit.s.discord_token)
        self.port = GuildPort(self)
        self.cards = raffles.Cards(self)
        self._saved: dict[str, Any] = {}
        self._saved_mtime = -1.0
        self._tasks: set[asyncio.Task] = set()

    # where things are ---------------------------------------------------------------------------------
    @property
    def guild(self) -> Any | None:
        return self.get_guild(self.s.discord.guild_id)

    def saved(self) -> dict[str, Any]:
        """data/state.json written by `kit.setup build`, re-read when it changes."""
        path = self.data_dir / "state.json"
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return {}
        if mtime != self._saved_mtime:
            self._saved, self._saved_mtime = statefile.load(path), mtime
        return self._saved

    def role(self, name: str) -> Any | None:
        """A role by config name: the id the builder saved, else the name (exact, then ignoring case)."""
        guild = self.guild
        if guild is None or not name:
            return None
        saved = str(self.saved().get("roles", {}).get(name, ""))
        role = guild.get_role(int(saved)) if saved.isdigit() else None
        return (role or discord.utils.get(guild.roles, name=name)
                or next((r for r in guild.roles if r.name.casefold() == name.casefold()), None))

    def channel(self, purpose: str) -> Any | None:
        """The text channel for a purpose in config [channels] (verify, giveaways, winners, tickets, ...)."""
        guild = self.guild
        if guild is None:
            return None
        found = statefile.find_channel_id(self.s.channels[purpose], self.saved().get("channels", {}),
                                          [(str(c.id), c.name) for c in guild.text_channels])
        return guild.get_channel(int(found)) if found else None

    def spawn(self, coro: Any) -> None:
        """Run a background task and keep a reference, so it is not garbage-collected."""
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # lifecycle ----------------------------------------------------------------------------------------
    async def setup_hook(self) -> None:
        verify.register(self)
        raffles.register(self)
        support.register(self)
        guild = discord.Object(id=self.s.discord.guild_id)
        self.tree.add_command(commands.build(self), guild=guild)
        self.tree.on_error = self.on_app_error
        await self.tree.sync(guild=guild)
        self.spawn(self.ticker())

    async def on_ready(self) -> None:
        if self.guild is None:
            log.error("the bot is not in server %s: open the link from `python -m kit.setup invite-url`",
                      self.s.discord.guild_id)
        else:
            log.info("online as %s; raffles: %d", self.user, len(self.kit.store.raffle_ids()))

    async def on_member_join(self, member: Any) -> None:
        """Someone who linked a wallet before joining gets their roles at once."""
        if member.guild.id == self.s.discord.guild_id and not member.bot and self.kit.store.wallets_of(str(member.id)):
            report = await self.kit.sync_user(str(member.id), "linked member joined")
            log.info("member joined: %s, %s", member.name, report.summary())

    async def ticker(self) -> None:
        """Every 30 s: draw what is due, redraw pending cards, give freshly linked members their roles;
        every `sync_minutes`: the full holder sync."""
        await self.wait_until_ready()
        ticks, known = 0, None
        while not self.is_closed():
            await self.tick(ticks, known)
            ticks = (ticks + 1) % max(1, self.s.sync_minutes * 60 // TICK)
            known = set(self.kit.store.owners())
            await asyncio.sleep(TICK)

    async def tick(self, ticks: int, known: set[str] | None) -> None:
        for r in self.kit.due_raffles():
            try:  # each raffle on its own: one that keeps failing must not hold up the others
                await raffles.finish(self, r)
            except Exception:  # noqa: BLE001
                log.exception("raffle %s: the draw failed; trying again on the next tick", r.id)
        try:
            await self.cards.flush()
        except Exception as e:  # noqa: BLE001
            log.warning("pending cards: %s", e)
        if ticks == 0:
            try:
                log.info("holder sync: %s", await self.kit.sync_all())
            except Exception:  # noqa: BLE001
                log.exception("holder sync")
        elif known is not None:
            # Members who linked a wallet since the last tick get their roles now, even when the website
            # could not reach Discord at that moment.
            fresh = set(self.kit.store.owners()) - known
            if fresh:
                try:
                    await self.kit.sync_users(sorted(fresh), "auto after link")
                    log.info("fresh links: %s", sorted(fresh))
                except Exception as e:  # noqa: BLE001
                    log.warning("fresh-link check: %s", e)

    async def on_app_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        log.exception("command error: %s", error, exc_info=error)
        text = f"Something went wrong: {str(error)[:150]}"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except discord.HTTPException:
            pass
