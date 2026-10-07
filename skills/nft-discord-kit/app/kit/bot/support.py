"""Support tickets and self-roles.

Tickets: a panel in the tickets channel with a button per category. A press asks for the question in
a modal and opens a PRIVATE thread named "<Category> | <name>", adds the person, pings the team (a
mentionable staff role is mentioned, otherwise its members one by one) and posts the question with
the holder context the bot knows: NFTs on chain, roles, linked wallets. One open ticket per person.
Close (the button or /nft tickets close; the team or the author): the transcript goes to
data/tickets/ as JSON and to the log channel as a text file, the author leaves the thread, and the
thread is archived (and locked when the bot may).
"""
from __future__ import annotations

import io
import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any

import discord

from ..config import TicketCategory
from ..panels import ROLE_PREFIX, TICKET_CLOSE_ID, TICKET_PREFIX, slugify, tickets_panel
from ..raffles import short, wallet_text
from ..support import archive_minutes, thread_name, transcript
from .common import as_member, is_admin, is_support_staff, kinds_used, plural

if TYPE_CHECKING:
    from .client import KitBot

log = logging.getLogger("kit.bot")


def register(bot: KitBot) -> None:
    bot.add_dynamic_items(TicketOpenButton, SelfRoleButton)
    bot.add_view(TicketView())


def category(bot: KitBot, slug: str) -> TicketCategory:
    """A category by its slug; the last one (usually "Other") when unknown."""
    cats = bot.s.support.categories
    return next((c for c in cats if slugify(c.name) == slug), cats[-1])


class TicketOpenButton(discord.ui.DynamicItem[discord.ui.Button], template=r"tk:open:(?P<slug>[a-z0-9-]+)"):
    def __init__(self, slug: str, label: str = "", emoji: str = ""):
        super().__init__(discord.ui.Button(label=label or slug, emoji=emoji or None, style=discord.ButtonStyle.secondary,
                                           custom_id=TICKET_PREFIX + slug))
        self.slug = slug

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]):
        return cls(match["slug"])

    async def callback(self, interaction: discord.Interaction) -> None:
        await ticket_start(interaction.client, interaction, self.slug)  # type: ignore[arg-type]


class TicketView(discord.ui.View):
    """Persistent: the Close button under the opening message."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Close ticket", style=discord.ButtonStyle.danger, custom_id=TICKET_CLOSE_ID,
                       emoji="\U0001F512")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await ticket_close(interaction.client, interaction)  # type: ignore[arg-type]


class TicketModal(discord.ui.Modal):
    question = discord.ui.TextInput(label="What is it about?", style=discord.TextStyle.paragraph,
                                    placeholder="A few lines: what happened, links, the wallet if it matters",
                                    min_length=5, max_length=1000)

    def __init__(self, bot: KitBot, cat: TicketCategory):
        super().__init__(title=f"New ticket · {cat.name}"[:45])
        self.bot, self.cat = bot, cat

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # Discord wants an answer within 3 seconds; the thread and the log take longer: defer first.
        await interaction.response.defer(ephemeral=True, thinking=True)
        await ticket_open(self.bot, interaction, self.cat, str(self.question.value).strip())


async def open_tickets(bot: KitBot, user_id: str, fetch: bool = True) -> list[Any]:
    """The person's open ticket threads that still exist and are not archived; a thread that is gone or
    archived closes its record on the way. `fetch=False` looks at the cache only and changes nothing
    (for the button, which must open its modal within 3 seconds)."""
    out = []
    for rec in bot.kit.store.open_tickets(user_id):
        thread = bot.get_channel(int(rec["thread"]))
        if thread is None and not fetch:
            continue
        if thread is None:
            try:
                thread = await bot.fetch_channel(int(rec["thread"]))
            except discord.HTTPException:
                thread = None
        if thread is None or getattr(thread, "archived", False):
            rec.update(status="closed", closed_at=int(time.time()), closed_by="gone" if thread is None else "archived")
            bot.kit.store.save_ticket(rec)
            continue
        out.append(thread)
    return out


async def ticket_start(bot: KitBot, interaction: discord.Interaction, slug: str) -> None:
    member = as_member(interaction.user)
    if member is None or not bot.s.support.enabled:
        await interaction.response.send_message("Tickets are opened from the server.", ephemeral=True)
        return
    mine = await open_tickets(bot, str(member.id), fetch=False)  # ticket_open checks again, fetching
    if len(mine) >= bot.s.support.max_open and not is_support_staff(bot, member):
        await interaction.response.send_message(
            f"You already have an open ticket: {mine[0].mention} — write there, or close it first.", ephemeral=True)
        return
    await interaction.response.send_modal(TicketModal(bot, category(bot, slug)))


def holder_context(bot: KitBot, member: Any) -> tuple[str, str, str]:
    """(holdings, roles, wallets) for the opening message: what the bot itself knows about this person."""
    kit, s = bot.kit, bot.s
    uid = str(member.id)
    h = kit.last_holdings(uid)
    if h:
        held_specials = [sp.name for sp in s.specials if h.specials.get(sp.name)]
        held = (f"{h.total} NFT{'' if h.total == 1 else 's'}" + "".join(f" · {n}" for n in held_specials)
                + f" · read <t:{h.at}:R>")
    else:
        held = "not verified yet"
    managed = {name.casefold() for name in s.holder_roles()}
    roles = ", ".join(r.name for r in member.roles if r.name.casefold() in managed) or "none"
    linked = kit.store.wallets_of(uid)
    if not linked:
        return held, roles, "no wallet linked"
    wallets = ", ".join(f"`{short(w.address)}`" for w in linked[:4]) + (f" +{len(linked) - 4}" if len(linked) > 4 else "")
    for kind in kinds_used(s):
        wallet, own = kit.raffle_wallet(uid, kind)
        if own:
            wallets += f"\n{s.kind_label(kind)} raffle wallet `{short(wallet_text(wallet))}`"
    return held, roles, wallets


def staff_pings(bot: KitBot, author: Any) -> tuple[list[str], list[Any]]:
    """Mentions for the team: a mentionable staff role as itself, otherwise its members one by one
    (the author is not pinged twice). Returns (mentions, roles to allow)."""
    mentions, roles, people = [], [], set()
    for name in bot.s.support.staff_roles:
        role = bot.role(name)
        if role is None:
            continue
        if role.mentionable:
            mentions.append(role.mention)
            roles.append(role)
        else:
            people |= {m.id for m in role.members if not m.bot and m.id != author.id}
    return mentions + [f"<@{i}>" for i in sorted(people)], roles


async def ticket_log(bot: KitBot, title: str, line: str, text: str = "", file: discord.File | None = None) -> None:
    channel = bot.channel("ticket_log")
    if channel is None:
        return
    e = discord.Embed(title=title, color=bot.s.project.color, description=line + (f"\n\n{text}" if text else ""))
    kw: dict[str, Any] = {"embed": e, "allowed_mentions": discord.AllowedMentions.none()}
    if file is not None:
        kw["file"] = file
    try:
        await channel.send(**kw)
    except discord.HTTPException as err:
        log.warning("ticket log: %s", err)


async def ticket_open(bot: KitBot, interaction: discord.Interaction, cat: TicketCategory, question: str) -> None:
    kit, s = bot.kit, bot.s
    member = as_member(interaction.user)
    channel = bot.channel("tickets") or (interaction.channel if hasattr(interaction.channel, "create_thread") else None)
    if channel is None:
        await interaction.followup.send("The tickets channel is not set up — tell the team in chat.", ephemeral=True)
        return
    mine = await open_tickets(bot, str(member.id))
    if len(mine) >= s.support.max_open and not is_support_staff(bot, member):
        await interaction.followup.send(f"You already have an open ticket: {mine[0].mention}", ephemeral=True)
        return
    n = kit.store.next_ticket_number()
    try:
        thread = await channel.create_thread(name=thread_name(cat.name, member.display_name),
                                             type=discord.ChannelType.private_thread,
                                             auto_archive_duration=archive_minutes(s.support.archive_hours),
                                             invitable=False, reason=f"ticket #{n} by {member.name}")
    except discord.Forbidden:
        log.warning("ticket #%d: no right to open a private thread in #%s", n, channel.name)
        await interaction.followup.send(f"Could not open a thread in {channel.mention} — the bot lacks Create "
                                        "Private Threads there. Ping the team in chat for now.", ephemeral=True)
        return
    except discord.HTTPException as e:
        log.warning("ticket #%d: create_thread: %s", n, e)
        await interaction.followup.send(f"Could not open a ticket right now ({e.text or e.status}). Try again in a "
                                        "minute.", ephemeral=True)
        return
    kit.store.save_ticket({"n": n, "thread": str(thread.id), "channel": str(channel.id), "user": str(member.id),
                           "name": member.name, "cat": cat.name, "question": question[:1000], "at": int(time.time()),
                           "status": "open"})
    held, roles, wallets = holder_context(bot, member)
    e = discord.Embed(title=f"{cat.emoji} Ticket #{n} · {cat.name}".strip(), color=s.project.color,
                      description=question[:4000])
    e.add_field(name="From", value=f"{member.mention} (`{member.name}`)", inline=True)
    e.add_field(name=f"{s.project.name} on chain", value=held, inline=True)
    e.add_field(name="Roles", value=roles[:1024], inline=True)
    e.add_field(name="Wallets", value=wallets[:1024], inline=False)
    e.set_footer(text=f"{s.project.name} · support · close it with the button once solved")
    pings, ping_roles = staff_pings(bot, member)
    try:
        await thread.send(content=" ".join([member.mention] + pings), embed=e, view=TicketView(),
                          allowed_mentions=discord.AllowedMentions(users=True, roles=ping_roles, everyone=False))
        await thread.send(f"**{s.project.name}:** {s.support.greeting}")
    except discord.HTTPException as err:
        log.warning("ticket #%d: first message: %s", n, err)
    try:
        await thread.add_user(member)
    except discord.HTTPException:
        pass  # the mention above already pulled them in
    log.info("ticket #%d opened by %s: %s", n, member.name, cat.name)
    await ticket_log(bot, f"{cat.emoji} Ticket #{n} opened · {cat.name}".strip(),
                     f"{member.mention} · {thread.mention} · {thread.jump_url}", question[:300])
    await interaction.followup.send(f"Your ticket is open: {thread.mention} — the team is pinged there.",
                                    ephemeral=True)


async def ticket_close(bot: KitBot, interaction: discord.Interaction, note: str | None = None) -> None:
    kit = bot.kit
    thread = interaction.channel
    rec = kit.store.ticket(str(getattr(thread, "id", "")))
    if rec is None:
        await interaction.response.send_message("This is not a ticket thread of mine.", ephemeral=True)
        return
    if not (is_support_staff(bot, interaction.user) or str(interaction.user.id) == rec.get("user")):
        await interaction.response.send_message("Only the team or the person who opened it can close a ticket.",
                                                ephemeral=True)
        return
    if rec.get("status") != "open":
        await interaction.response.send_message("Already closed.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    rows = []
    try:
        async for m in thread.history(limit=None, oldest_first=True):
            text = m.content or ""
            for em in m.embeds:
                if em.description:
                    text += ("\n" if text else "") + em.description
            rows.append({"at": int(m.created_at.timestamp()), "who": str(m.author.id), "name": m.author.name,
                         "bot": bool(m.author.bot), "text": text[:4000], "files": [a.url for a in m.attachments]})
    except discord.HTTPException as e:
        log.warning("ticket #%s: history: %s", rec.get("n"), e)
    people = [row for row in rows if not row["bot"]]
    blank = bool(people) and not any(row["text"] or row["files"] for row in people)
    if blank:  # without the MESSAGE CONTENT intent Discord hands out other people's messages empty
        log.warning("ticket #%s: every member message came back empty: turn on MESSAGE CONTENT INTENT "
                    "(Developer Portal > Bot) so transcripts keep the text", rec.get("n"))
    rec.update(status="closed", closed_at=int(time.time()), closed_by=str(interaction.user.id),
               closed_name=interaction.user.name, messages=len(rows), note=(note or "")[:300])
    folder = bot.data_dir / "tickets"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{int(rec['n']):04d}-{rec['thread']}.json").write_text(
            json.dumps({"ticket": rec, "messages": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        log.warning("ticket #%s: transcript file: %s", rec.get("n"), e)
    kit.store.save_ticket(rec)
    cat = category(bot, slugify(rec.get("cat", "")))
    guild = bot.guild

    def name_of(uid: str) -> str:
        found = guild.get_member(int(uid)) if guild else None
        return found.name if found else uid

    head = (f"Ticket #{int(rec['n'])} · {rec.get('cat')} · opened by {rec.get('name')} · closed by "
            f"{interaction.user.name}")
    if blank:
        head += ("\n(Members' message text came back empty: turn on MESSAGE CONTENT INTENT in the Developer "
                 "Portal, Bot tab, so transcripts keep it.)")
    data = io.BytesIO(transcript(head, rows, name_of).encode("utf-8"))
    await ticket_log(bot, f"{cat.emoji} Ticket #{int(rec['n'])} closed · {rec.get('cat')}".strip(),
                     f"<@{rec.get('user')}> · closed by {interaction.user.mention} · "
                     f"{plural(len(rows), 'message')}" + (f" · {note}" if note else ""),
                     file=discord.File(data, filename=f"ticket-{int(rec['n']):04d}.txt"))
    await interaction.followup.send("Closed — the transcript went to the log.", ephemeral=True)
    try:
        await thread.send(f"Ticket closed by {interaction.user.mention}" + (f" — {note}" if note else "")
                          + ". Thanks!", allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        pass
    author = guild.get_member(int(rec["user"])) if guild and str(rec.get("user", "")).isdigit() else None
    if author and not is_support_staff(bot, author):  # the author leaves; the team keeps the thread and its history
        try:
            await thread.remove_user(author)
        except discord.HTTPException:
            pass
    try:
        await thread.edit(archived=True, locked=True)
    except discord.Forbidden:  # no Manage Threads: archive only
        try:
            await thread.edit(archived=True)
        except discord.HTTPException as e:
            log.warning("ticket #%s: archive: %s", rec.get("n"), e)
    except discord.HTTPException as e:
        log.warning("ticket #%s: archive: %s", rec.get("n"), e)
    log.info("ticket #%s closed by %s (%d messages)", rec.get("n"), interaction.user.name, len(rows))


def ticket_panel_view(bot: KitBot) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for c in bot.s.support.categories[:5]:
        view.add_item(TicketOpenButton(slugify(c.name), c.name, c.emoji))
    return view


async def cmd_tickets_panel(bot: KitBot, interaction: discord.Interaction) -> None:
    if not is_admin(bot, interaction.user):
        await interaction.response.send_message("Team only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)  # the post itself may take over 3 s
    payload = tickets_panel(bot.s)
    try:
        await interaction.channel.send(embed=discord.Embed.from_dict(payload["embeds"][0]), view=ticket_panel_view(bot))
    except discord.HTTPException as e:
        await interaction.followup.send(f"Could not post here: {e.text or e.status}. Give the bot View / Send / Embed "
                                        "Links in this channel.", ephemeral=True)
        return
    await interaction.followup.send("Panel posted.", ephemeral=True)


# self-roles ------------------------------------------------------------------------------------------
class SelfRoleButton(discord.ui.DynamicItem[discord.ui.Button], template=r"kit:role:(?P<slug>[a-z0-9-]+)"):
    def __init__(self, slug: str):
        super().__init__(discord.ui.Button(label=slug, style=discord.ButtonStyle.secondary, custom_id=ROLE_PREFIX + slug))
        self.slug = slug

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]):
        return cls(match["slug"])

    async def callback(self, interaction: discord.Interaction) -> None:
        await toggle_self_role(interaction.client, interaction, self.slug)  # type: ignore[arg-type]


async def toggle_self_role(bot: KitBot, interaction: discord.Interaction, slug: str) -> None:
    """A self-roles button: take the role, or drop it when you already have it."""
    wanted = next((r for r in bot.s.self_roles if slugify(r.name, "role") == slug), None)
    role = bot.role(wanted.name) if wanted else None
    member = as_member(interaction.user)
    if role is None or member is None:
        await interaction.response.send_message("This role is not available right now.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)  # the role change is a Discord call
    try:
        if any(r.id == role.id for r in member.roles):
            await member.remove_roles(role, reason="self-role")
            text = f"Removed {role.mention}."
        else:
            await member.add_roles(role, reason="self-role")
            text = f"You now have {role.mention}."
    except discord.Forbidden:
        text = "I cannot manage that role: my own role must sit above it (Server Settings > Roles)."
    except discord.HTTPException as e:
        text = f"Discord did not take that right now ({e.status}). Try again in a minute."
    await interaction.followup.send(text, ephemeral=True)
