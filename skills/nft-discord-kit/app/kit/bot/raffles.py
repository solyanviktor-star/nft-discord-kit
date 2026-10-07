"""Raffle cards, their buttons, the draw announcement and the /nft raffle commands."""
from __future__ import annotations

import asyncio
import io
import logging
import re
import time
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands

from ..holdings import tickets_rule
from ..panels import ENTER_PREFIX, RAFFLE_WALLET_PREFIX, slugify, verify_panel
from ..raffles import (CARD_OLD_GAP, CARD_YOUNG_GAP, ENDED, OPEN, Raffle, card_embed, export_csv,
                       mention_chunks, parse_duration, snowflake_time, wallet_columns, winners_embed)
from ..service import EnterResult
from .common import as_member, can_run_raffles, member_info
from .verify import LinkView, VerifyPanel, show_wallets

if TYPE_CHECKING:
    from .client import KitBot

log = logging.getLogger("kit.bot")


def register(bot: KitBot) -> None:
    bot.add_dynamic_items(EnterButton, WalletButton)


def brand(bot: KitBot) -> str:
    return f"{bot.s.project.name} Raffles"


def card(bot: KitBot, r: Raffle) -> discord.Embed:
    labels = {sp.name: (role.mention if (role := bot.role(sp.role)) else sp.name) for sp in bot.s.specials}
    return discord.Embed.from_dict(card_embed(r, bot.kit.store.entry_count(r.id), [f"<@&{x}>" for x in r.eligible],
                                              brand(bot), bot.s.project.color, tickets_rule(bot.s, labels)))


class RaffleView(discord.ui.View):
    def __init__(self, rid: str, ended: bool = False):
        super().__init__(timeout=None)
        self.add_item(EnterButton(rid, ended))
        self.add_item(WalletButton(rid))


class EnterButton(discord.ui.DynamicItem[discord.ui.Button], template=r"rf:enter:(?P<id>[0-9a-f]+)"):
    def __init__(self, rid: str, ended: bool = False):
        super().__init__(discord.ui.Button(label="Enter", style=discord.ButtonStyle.success,
                                           custom_id=ENTER_PREFIX + rid, disabled=ended))
        self.rid = rid

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]):
        r = interaction.client.kit.store.raffle(match["id"])  # type: ignore[attr-defined]
        return cls(match["id"], ended=bool(r and r.status != OPEN))

    async def callback(self, interaction: discord.Interaction) -> None:
        await enter_raffle(interaction.client, interaction, self.rid)  # type: ignore[arg-type]


class WalletButton(discord.ui.DynamicItem[discord.ui.Button], template=r"rf:wallet:(?P<id>[0-9a-f]+)"):
    def __init__(self, rid: str):
        super().__init__(discord.ui.Button(label="Change Wallet", style=discord.ButtonStyle.secondary,
                                           custom_id=RAFFLE_WALLET_PREFIX + rid))
        self.rid = rid

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]):
        return cls(match["id"])

    async def callback(self, interaction: discord.Interaction) -> None:
        await show_wallets(interaction.client, interaction)  # type: ignore[arg-type]


def verify_mention(bot: KitBot) -> str:
    channel = bot.channel("verify")
    return channel.mention if channel else f"#{bot.s.channels['verify']}"


async def enter_raffle(bot: KitBot, interaction: discord.Interaction, rid: str) -> None:
    """The Enter button: the shared entry door (`Kit.enter`), then the replies."""
    kit = bot.kit
    r = kit.store.raffle(rid)
    if not r or r.status != OPEN:
        await interaction.response.send_message("This raffle has ended.", ephemeral=True)
        return
    if rid in kit.drawing or r.ends <= time.time():
        await interaction.response.send_message("This raffle is closing right now.", ephemeral=True)
        return
    member = as_member(interaction.user)
    info = member_info(member) if member else None
    if info is None or (r.eligible and not info.role_ids & set(r.eligible)):
        await interaction.response.send_message(kit.entry_text(r, EnterResult("not_eligible"), verify_mention(bot)),
                                                ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    res = await kit.enter(rid, info)
    text = kit.entry_text(r, res, verify_mention(bot))
    if res.code in ("closed", "closing"):
        text = "This raffle has ended."
    views = {"no_profile": lambda: LinkView(bot, interaction.user, with_kinds=False),
             "no_evm_wallet": lambda: LinkView(bot, interaction.user, with_kinds=False),
             "no_wallet": lambda: LinkView(bot, interaction.user)}
    extra: dict[str, Any] = {"view": views[res.code]()} if res.code in views else {}
    await interaction.followup.send(text, ephemeral=True, **extra)


class Cards:
    """Cards are redrawn on a schedule, not on every entry: a young card at most every 3 minutes, one older
    than an hour every 15. A status change (ended, cancelled) redraws at once. A card deleted in Discord
    (404 Unknown Message) is not redrawn again until a new card is posted."""

    def __init__(self, bot: KitBot):
        self.bot = bot
        self.last: dict[str, float] = {}  # raffle id -> when its card was last redrawn
        self.pending: set[str] = set()  # raffle ids whose redraw waits for the interval

    async def refresh(self, r: Raffle | None, force: bool = False) -> None:
        if r is None:
            return
        gap = CARD_OLD_GAP if time.time() - snowflake_time(r.message_id) > 3600 else CARD_YOUNG_GAP
        if not force and time.time() - self.last.get(r.id, 0.0) < gap:
            self.pending.add(r.id)
            return
        self.pending.discard(r.id)
        self.last[r.id] = time.time()
        if not r.message_id or r.card_gone == r.message_id:
            return
        try:
            channel = self.bot.get_channel(int(r.channel_id)) or await self.bot.fetch_channel(int(r.channel_id))
            await channel.get_partial_message(int(r.message_id)).edit(embed=card(self.bot, r),
                                                                      view=RaffleView(r.id, ended=r.status != OPEN))
        except discord.NotFound as e:
            r.card_gone = r.message_id
            self.bot.kit.store.save_raffle(r)
            log.warning("refresh %s: the card message is gone (%s); no more redraws, the raffle itself stays %s",
                        r.id, e, r.status)
        except discord.HTTPException as e:
            log.warning("refresh %s: %s", r.id, e)

    async def flush(self) -> None:
        """Redraws the cards whose interval has passed (called by the ticker)."""
        for rid in list(self.pending):
            r = self.bot.kit.store.raffle(rid)
            if r is None:
                self.pending.discard(rid)
                continue
            before = len(self.pending)
            await self.refresh(r)
            if len(self.pending) < before:
                await asyncio.sleep(0.5)


async def finish(bot: KitBot, r: Raffle, reroll_n: int | None = None) -> tuple[list[str], list[str]]:
    """The draw. While it runs the raffle takes no entries: nobody slips in between the recount and the pick."""
    kit = bot.kit
    if r.id in kit.drawing:
        return [], []
    kit.drawing.add(r.id)
    try:
        return await _finish_draw(bot, r, reroll_n)
    finally:
        kit.drawing.discard(r.id)


class AnnounceError(Exception):
    """The winners cannot be announced; the raffle stays open and the ticker tries again later."""


async def winners_channel(bot: KitBot, r: Raffle) -> Any:
    """[channels] winners, else the raffle's own channel (fetched when it is not in the cache)."""
    channel = bot.channel("winners") or bot.get_channel(int(r.channel_id))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(r.channel_id))
        except discord.HTTPException:
            channel = None
    if channel is None:
        raise AnnounceError("no channel to announce the winners in: set [channels] winners in config.toml, or let "
                            "the bot see the raffle's channel")
    return channel


async def _finish_draw(bot: KitBot, r: Raffle, reroll_n: int | None) -> tuple[list[str], list[str]]:
    kit = bot.kit
    win_ch = await winners_channel(bot, r)  # before the draw: no recount when it could not be announced
    if reroll_n is None:
        gtd, fcfs = await kit.draw(r)
    else:
        gtd, fcfs = await kit.draw(r, exclude=kit.previous_winners(r), n_gtd=reroll_n, n_fcfs=0)
    users = gtd + fcfs
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="View Giveaway", style=discord.ButtonStyle.link,
                                    url=f"https://discord.com/channels/{bot.s.discord.guild_id}/{r.channel_id}/"
                                        f"{r.message_id}"))
    # Discord caps message content at 2000 characters: 100 winners of mentions do not fit in one message.
    chunks = mention_chunks(users)
    mentions = discord.AllowedMentions(users=True, roles=False, everyone=False)
    embed = winners_embed(r, gtd, fcfs, brand(bot), "REROLL · " if reroll_n is not None else "")
    try:
        msg = await win_ch.send(content=chunks[0] if chunks else "No eligible entrants.",
                                embed=discord.Embed.from_dict(embed), view=view, allowed_mentions=mentions)
        for extra in chunks[1:]:
            await win_ch.send(content=extra, allowed_mentions=mentions)
    except discord.Forbidden as e:
        raise AnnounceError(f"the bot may not post in #{win_ch.name} ({e.text}): give it View Channel, Send "
                            "Messages and Embed Links there") from e
    fresh = kit.store.raffle(r.id) or r
    if reroll_n is None:
        kit.close_raffle(fresh, gtd, fcfs, str(msg.id), str(win_ch.id))
    else:
        kit.add_reroll(fresh, gtd)
    await bot.cards.refresh(kit.store.raffle(r.id), force=True)
    log.info("raffle %s: %s -> gtd %s fcfs %s", r.id, "reroll" if reroll_n else "ended", gtd, fcfs)
    return gtd, fcfs


def giveaway_tag(bot: KitBot, channel: Any) -> tuple[str | None, discord.AllowedMentions]:
    """Every new card tags the self-assigned alerts role (raffles.alert_role). A role that is not mentionable
    needs the bot to have "Mention @everyone, @here and All Roles" in that channel; without it the card goes
    out untagged and the log says so."""
    role = bot.role(bot.s.raffles.alert_role) if bot.s.raffles.alert_role else None
    if role is None:
        return None, discord.AllowedMentions.none()
    if not (role.mentionable or channel.permissions_for(channel.guild.me).mention_everyone):
        log.warning("giveaway tag skipped in #%s: the bot may not mention roles there", channel.name)
        return None, discord.AllowedMentions.none()
    return role.mention, discord.AllowedMentions(roles=True, everyone=False, users=False)


def default_eligible(bot: KitBot) -> list[str]:
    """Role ids for a new raffle: raffles.eligible_roles, else every holder role (or anyone, when holding
    is not required)."""
    names = list(bot.s.raffles.eligible_roles) or (bot.s.holder_roles() if bot.s.raffles.require_holding else [])
    return [str(role.id) for role in (bot.role(n) for n in names) if role is not None]


def rid_clean(value: str) -> str:
    """An id from a command: strip spaces, quotes, backticks and case."""
    return re.sub(r"[^0-9a-f]", "", str(value or "").lower())


async def rid_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    kit = interaction.client.kit  # type: ignore[attr-defined]
    counts = kit.store.entry_counts()
    return [app_commands.Choice(name=f"{r.id} · {r.title[:50]} · {r.status} · "
                                     f"{counts.get(r.id, 0)} entrants"[:100], value=r.id)
            for r in sorted(kit.find_raffles(current), key=lambda x: -x.created)]


async def _denied(bot: KitBot, interaction: discord.Interaction) -> bool:
    if can_run_raffles(bot, interaction.user):
        return False
    await interaction.response.send_message("Team only.", ephemeral=True)
    return True


async def cmd_create(bot: KitBot, interaction: discord.Interaction, title: str, link: str, chain: str, duration: str,
                     gtd: int, fcfs: int = 0, description: str | None = None, image: str | None = None,
                     roles: str | None = None, channel: Any = None) -> None:
    if await _denied(bot, interaction):
        return
    kit = bot.kit
    ch = channel or bot.channel("giveaways") or interaction.channel
    eligible = re.findall(r"<@&(\d+)>", roles or "") or default_eligible(bot)
    try:
        r = kit.create_raffle(title=title, link=link, chain=chain, duration=parse_duration(duration), gtd=gtd,
                              fcfs=fcfs, created_by=str(interaction.user.id), channel_id=str(ch.id), eligible=eligible,
                              description=description or "", image=image or "")
    except ValueError as e:
        await interaction.response.send_message(str(e), ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    content, mentions = giveaway_tag(bot, ch)
    try:
        msg = await ch.send(content=content, embed=card(bot, r), view=RaffleView(r.id), allowed_mentions=mentions)
    except discord.HTTPException as e:
        kit.store.delete_raffle(r.id)
        await interaction.followup.send(f"Could not post in {ch.mention}: {e.text or e.status}. Give the bot View / "
                                        "Send / Embed Links in that channel and try again.", ephemeral=True)
        return
    r.message_id = str(msg.id)
    kit.store.save_raffle(r)
    await interaction.followup.send(f"Raffle `{r.id}` posted in {ch.mention} — ends <t:{r.ends}:f>.",
                                    ephemeral=True)


async def cmd_end(bot: KitBot, interaction: discord.Interaction, raffle: str) -> None:
    if await _denied(bot, interaction):
        return
    r = bot.kit.store.raffle(rid_clean(raffle))
    if not r or r.status != OPEN or r.id in bot.kit.drawing:
        await interaction.response.send_message("No open raffle with that id.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    gtd, fcfs = await finish(bot, r)
    await interaction.followup.send(f"Ended `{r.id}`: {len(gtd)} GTD, {len(fcfs)} FCFS winners posted.", ephemeral=True)


async def cmd_reroll(bot: KitBot, interaction: discord.Interaction, raffle: str, count: int = 1) -> None:
    if await _denied(bot, interaction):
        return
    r = bot.kit.store.raffle(rid_clean(raffle))
    if not r or r.status != ENDED:
        await interaction.response.send_message("No ended raffle with that id.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    gtd, _ = await finish(bot, r, reroll_n=max(1, count))
    await interaction.followup.send(f"Rerolled `{r.id}`: {len(gtd)} new winner(s).", ephemeral=True)


async def cmd_export(bot: KitBot, interaction: discord.Interaction, raffle: str, everyone: bool = False) -> None:
    if await _denied(bot, interaction):
        return
    kit, s = bot.kit, bot.s
    r = kit.store.raffle(rid_clean(raffle))
    if not r:
        await interaction.response.send_message("No raffle with that id.", ephemeral=True)
        return
    keys = [f.key for f in s.wallet_kinds[r.wallet_kind].fields] if r.wallet_kind in s.wallet_kinds else []
    all_entrants = everyone or not r.result
    data = export_csv(r, kit.store.entries(r.id), wallet_columns(r.wallet_kind, keys), all_entrants)
    which = "entrants" if all_entrants else "winners"
    name = f"{slugify(r.title, 'raffle')}-x-{slugify(s.project.name, 'project')}-{which}-{r.id}.csv"
    await interaction.response.send_message(f"{r.title} · {r.chain} · {'all entrants' if all_entrants else 'winners'}",
                                            file=discord.File(io.BytesIO(data.encode("utf-8")), filename=name),
                                            ephemeral=True)


async def cmd_list(bot: KitBot, interaction: discord.Interaction) -> None:
    if await _denied(bot, interaction):
        return
    counts = bot.kit.store.entry_counts()
    rows = [f"`{r.id}` {r.title} · {r.chain} · {r.status.upper()} · {counts.get(r.id, 0)} entrants "
            f"· ends <t:{r.ends}:R>" for r in bot.kit.store.raffles(limit=25)]
    await interaction.response.send_message("\n".join(rows)[:2000] or "No raffles yet.", ephemeral=True)


async def cmd_cancel(bot: KitBot, interaction: discord.Interaction, raffle: str) -> None:
    if await _denied(bot, interaction):
        return
    r = bot.kit.store.raffle(rid_clean(raffle))
    if not r or r.status != OPEN or r.id in bot.kit.drawing:
        await interaction.response.send_message("No open raffle with that id.", ephemeral=True)
        return
    bot.kit.cancel_raffle(r)
    await interaction.response.send_message(f"Cancelled `{r.id}`.", ephemeral=True)
    await bot.cards.refresh(r, force=True)


async def cmd_panel(bot: KitBot, interaction: discord.Interaction) -> None:
    """Post the verification panel in this channel (`kit.setup panels` does the same over REST)."""
    if await _denied(bot, interaction):
        return
    await interaction.response.defer(ephemeral=True, thinking=True)  # the post itself may take over 3 s
    role_ids = {name: str(role.id) for name in bot.s.holder_roles() if (role := bot.role(name))}
    payload = verify_panel(bot.s, role_ids)
    try:
        await interaction.channel.send(embed=discord.Embed.from_dict(payload["embeds"][0]), view=VerifyPanel())
    except discord.HTTPException as e:
        await interaction.followup.send(f"Could not post here: {e.text or e.status}. Give the bot View / Send / Embed "
                                        "Links in this channel.", ephemeral=True)
        return
    await interaction.followup.send("Panel posted.", ephemeral=True)


def giveaways_embed(bot: KitBot, user_id: str) -> discord.Embed:
    """/nft giveaways: what is open and whether this person is in."""
    kit = bot.kit
    tickets, parts = kit.tickets_of(user_id)
    e = discord.Embed(title="Active giveaways", color=bot.s.project.color)
    open_ = sorted(kit.store.raffles(OPEN), key=lambda x: x.ends)
    if not open_:
        channel = bot.channel("giveaways")
        e.description = "Nothing running right now." + (f" Watch {channel.mention}." if channel else "")
    else:
        lines = []
        for r in open_:
            jump = f"https://discord.com/channels/{bot.s.discord.guild_id}/{r.channel_id}/{r.message_id}"
            mine = kit.store.entry(r.id, user_id)
            lines.append(f"• [{r.title}]({jump}) · {r.chain} · ends <t:{r.ends}:R> · "
                         + (f"**IN** ({mine.tickets} tickets)" if mine else "not entered"))
        e.description = "\n".join(lines)[:4096]
    e.set_footer(text=f"Your raffle tickets: {tickets}" + (f" ({', '.join(parts)})" if parts else ""))
    return e
