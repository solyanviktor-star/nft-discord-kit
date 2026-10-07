"""The /nft slash commands. Each one is a thin wrapper around a module-level handler, so tests can call
the handlers directly with stand-in interactions."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands

from ..raffles import short, wallet_text
from . import raffles, support
from .common import as_member, is_admin, kinds_used
from .verify import LinkView, run_verify

if TYPE_CHECKING:
    from .client import KitBot


async def cmd_me(bot: KitBot, interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    kit, s = bot.kit, bot.s
    uid = str(interaction.user.id)
    tickets, parts = kit.tickets_of(uid)
    e = discord.Embed(title=f"{interaction.user.display_name} · profile", color=s.project.color)
    linked = kit.store.wallets_of(uid)
    if linked:
        for kind in kinds_used(s):
            wallet, own = kit.raffle_wallet(uid, kind)
            if kind == "evm":
                e.add_field(name="Raffle wallet (EVM)",
                            value=f"`{wallet_text(wallet)}`" + (" · burner" if own else " · primary"), inline=False)
            else:
                e.add_field(name=s.kind_label(kind), value=f"`{wallet_text(wallet)}`" if wallet else "not set",
                            inline=False)
        h = kit.last_holdings(uid)
        e.add_field(name=s.project.name, value=f"holder ×{h.total}" if h and h.total else "not found", inline=True)
    else:
        e.add_field(name="Wallet", value="not linked — press Link Wallet", inline=False)
    e.add_field(name="Raffle tickets", value=f"{tickets}" + (f" ({', '.join(parts)})" if parts else ""), inline=True)
    won = [f"• {r.title} — {kind} ({r.link})" for r, kind in kit.wins_of(uid)]
    e.add_field(name="Won", value="\n".join(won)[:1024] or "nothing yet", inline=False)
    await interaction.followup.send(embed=e, view=LinkView(bot, interaction.user, with_refresh=True, linked=bool(linked)),
                                    ephemeral=True)


async def cmd_check(bot: KitBot, interaction: discord.Interaction, user: Any) -> None:
    """Team: a member's wallets, per-wallet holdings read now, roles, entries and wins."""
    if not is_admin(bot, as_member(interaction.user)):
        await interaction.response.send_message("Team only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    kit, s = bot.kit, bot.s
    uid = str(user.id)
    e = discord.Embed(title=f"Check: {user.name}", color=s.project.color)
    member = await bot.port.member(uid)
    managed = {name.casefold() for name in s.holder_roles()}
    roles = [r.name for r in (member.roles if member else []) if r.name.casefold() in managed]
    e.add_field(name="Roles now", value=", ".join(roles) or "none", inline=False)
    linked = kit.store.wallets_of(uid)
    if not linked:
        e.description = ("**No wallet linked.** They have never signed in on the verification site from this Discord "
                         "account — roles cannot be granted automatically until they do.")
        await interaction.followup.send(embed=e, ephemeral=True)
        return
    single = await kit.reader.read({f"w{i}": [(w.kind, w.address)] for i, w in enumerate(linked)})
    per = [f"`{short(w.address)}` — " + (str(single[f"w{i}"].total) if single[f"w{i}"].complete else "?")
           for i, w in enumerate(linked)]
    total = sum(h.total for h in single.values()) if all(h.complete for h in single.values()) else None
    held = sorted({name for h in single.values() for name, n in h.specials.items() if n})
    e.add_field(name=f"Signed wallets ({len(linked)})", value="\n".join(per)[:1024], inline=False)
    e.add_field(name="On-chain total", value=(f"**{total}** NFT(s)" + (f" + {', '.join(held)}" if held else ""))
                if total is not None else "RPC busy, unknown", inline=True)
    for kind in kinds_used(s):
        if kind != "evm":
            wallet, _ = kit.raffle_wallet(uid, kind)
            e.add_field(name=s.kind_label(kind), value=f"`{wallet_text(wallet)}`" if wallet else "not set", inline=True)
    entered_ids = kit.store.entered_raffles(uid)
    mine = sorted(kit.store.raffles(), key=lambda r: -r.created)
    entered = [r.title[:22] for r in mine if r.id in entered_ids]
    won = [r.title[:22] for r, _ in kit.wins_of(uid)]
    e.add_field(name=f"Raffles entered ({len(entered)})", value=", ".join(entered[:6]) or "none", inline=False)
    e.add_field(name=f"Won ({len(won)})", value=", ".join(won[:6]) or "none", inline=False)
    if total == 0:
        e.set_footer(text="Zero on-chain: they sold, or the NFTs sit in a wallet they have not linked. Ask them to "
                          "link THAT wallet and press Verify.")
    await interaction.followup.send(embed=e, ephemeral=True)


def build(bot: KitBot) -> app_commands.Group:
    s = bot.s
    nft = app_commands.Group(name="nft", description=f"{s.project.name} raffles"[:100])
    raffle = app_commands.Group(name="raffle", description="Manage raffles (team)", parent=nft)
    tickets = app_commands.Group(name="tickets", description="Support tickets", parent=nft)
    chains = [app_commands.Choice(name=c.name, value=c.name) for c in s.raffle_chains]

    @nft.command(name="giveaways", description="Show active giveaways and whether you are in.")
    async def giveaways(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=raffles.giveaways_embed(bot, str(interaction.user.id)),
                                                ephemeral=True)

    @nft.command(name="verify", description=f"Re-check your {s.project.name} NFTs on-chain and update your roles."[:100])
    async def verify(interaction: discord.Interaction) -> None:
        await run_verify(bot, interaction, "manual /nft verify")

    @nft.command(name="me", description="Your wallets, tickets and giveaways you won.")
    async def me(interaction: discord.Interaction) -> None:
        await cmd_me(bot, interaction)

    @nft.command(name="check", description="Team: look up a member's wallets, on-chain holdings and roles.")
    @app_commands.describe(user="Member to check")
    async def check(interaction: discord.Interaction, user: discord.User) -> None:
        await cmd_check(bot, interaction, user)

    @raffle.command(name="create", description="Post a new raffle card.")
    @app_commands.describe(title="Project / raffle name", link="Project link (X, site)", chain="Chain of the prize",
                           duration="e.g. 24h, 2d 12h, 90m", gtd="Guaranteed spots", fcfs="FCFS spots",
                           description="Extra text under the link", image="Image URL",
                           roles="Eligible roles (mention them; default: all holder roles)",
                           channel="Channel to post in (default: the giveaways channel)")
    @app_commands.choices(chain=chains)
    async def create(interaction: discord.Interaction, title: str, link: str, chain: app_commands.Choice[str],
                     duration: str, gtd: int, fcfs: int = 0, description: str | None = None, image: str | None = None,
                     roles: str | None = None, channel: discord.TextChannel | None = None) -> None:
        await raffles.cmd_create(bot, interaction, title, link, chain.value, duration, gtd, fcfs, description, image,
                                 roles, channel)

    @raffle.command(name="end", description="End a raffle now and draw winners.")
    @app_commands.autocomplete(raffle=raffles.rid_autocomplete)
    async def end(interaction: discord.Interaction, raffle: str) -> None:
        await raffles.cmd_end(bot, interaction, raffle)

    @raffle.command(name="reroll", description="Draw extra winners for an ended raffle (previous winners excluded).")
    @app_commands.autocomplete(raffle=raffles.rid_autocomplete)
    async def reroll(interaction: discord.Interaction, raffle: str, count: int = 1) -> None:
        await raffles.cmd_reroll(bot, interaction, raffle, count)

    @raffle.command(name="export", description="CSV of winners' wallets (or all entrants).")
    @app_commands.autocomplete(raffle=raffles.rid_autocomplete)
    async def export(interaction: discord.Interaction, raffle: str, everyone: bool = False) -> None:
        await raffles.cmd_export(bot, interaction, raffle, everyone)

    @raffle.command(name="list", description="All raffles with ids and status.")
    async def list_raffles(interaction: discord.Interaction) -> None:
        await raffles.cmd_list(bot, interaction)

    @raffle.command(name="cancel", description="Cancel an open raffle without drawing.")
    @app_commands.autocomplete(raffle=raffles.rid_autocomplete)
    async def cancel(interaction: discord.Interaction, raffle: str) -> None:
        await raffles.cmd_cancel(bot, interaction, raffle)

    @raffle.command(name="panel", description="Post the wallet verification panel in this channel (team).")
    async def panel(interaction: discord.Interaction) -> None:
        await raffles.cmd_panel(bot, interaction)

    @tickets.command(name="panel", description="Post the support ticket panel in this channel (team).")
    async def tickets_panel(interaction: discord.Interaction) -> None:
        await support.cmd_tickets_panel(bot, interaction)

    @tickets.command(name="close", description="Close this ticket thread (team, or the person who opened it).")
    @app_commands.describe(note="A closing note for the log")
    async def tickets_close(interaction: discord.Interaction, note: str | None = None) -> None:
        await support.ticket_close(bot, interaction, note)

    return nft
