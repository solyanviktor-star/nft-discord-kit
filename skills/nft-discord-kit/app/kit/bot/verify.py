"""Wallets from Discord: the verify panel, Verify / My Wallets, the raffle-wallet modals.

A wallet is linked on the verification site (one signed message); these buttons hand out the
personal link, re-check holdings, show what is linked and set where raffle prizes go.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping
from urllib.parse import urlsplit

import discord

from ..panels import VERIFY_ID, WALLETS_ID
from ..raffles import short, wallet_text
from ..service import VERIFY_TTL
from .common import kinds_used, plural

if TYPE_CHECKING:
    from .client import KitBot


def register(bot: KitBot) -> None:
    bot.add_view(VerifyPanel())


def site(bot: KitBot) -> str:
    return urlsplit(bot.s.project.public_url).netloc or "the verification site"


class LinkView(discord.ui.View):
    """Stateless buttons; they live 30 minutes, just like the link."""

    def __init__(self, bot: KitBot, user: Any, with_kinds: bool = True, with_refresh: bool = False,
                 linked: bool = False, with_evm: bool | None = None):
        super().__init__(timeout=VERIFY_TTL)
        self.bot = bot
        self.add_item(discord.ui.Button(label="Link Another Wallet" if linked else "Link Wallet",
                                        style=discord.ButtonStyle.link,
                                        url=bot.kit.verify_url(str(user.id), user.name)))
        kinds = kinds_used(bot.s)
        if (linked if with_evm is None else with_evm) and "evm" in kinds:
            self._modal_button("Set Raffle Wallet", "evm")
        if with_kinds:
            for kind in kinds:
                if kind != "evm":
                    self._modal_button(f"Set {bot.s.kind_label(kind)} Wallet", kind)
        if with_refresh:
            refresh = discord.ui.Button(label="Refresh", style=discord.ButtonStyle.secondary)
            refresh.callback = self._refresh
            self.add_item(refresh)

    def _modal_button(self, label: str, kind: str) -> None:
        button = discord.ui.Button(label=label[:80], style=discord.ButtonStyle.primary)

        async def open_modal(interaction: discord.Interaction) -> None:
            await interaction.response.send_modal(wallet_modal(self.bot, kind))

        button.callback = open_modal
        self.add_item(button)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        await show_wallets(self.bot, interaction, refresh=True)


async def save_raffle_wallet(bot: KitBot, interaction: discord.Interaction, kind: str,
                             values: Mapping[str, str]) -> None:
    """The modals' submit: check the address, then save it and move it into the open raffles."""
    kit = bot.kit
    wallet, error = kit.parse_raffle_wallet(kind, values)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    uid = str(interaction.user.id)
    label = bot.s.kind_label(kind)
    if not kit.store.wallets_of(uid):
        what = "set the raffle wallet" if kind == "evm" else f"add {label}"
        await interaction.followup.send(f"Link your wallet first (Link Wallet), then {what}.", ephemeral=True,
                                        view=LinkView(bot, interaction.user, with_kinds=False))
        return
    moved = kit.set_raffle_wallet(uid, kind, wallet)
    updated = f"{plural(moved, 'open raffle')}" if moved else ""
    if kind == "evm":
        text = (f"Raffle wallet saved: `{wallet['address']}` — prizes go there. Tickets still come from the "
                "NFTs in your linked wallets." if wallet else
                "Raffle wallet reset — prizes go to your primary wallet again.")
        text += f" Updated on {updated}." if moved else ""
    elif kind == "solana":
        text = f"Solana wallet saved: `{wallet['address']}`" + (f" — updated on {updated}." if moved else "")
    else:
        fields = bot.s.wallet_kinds[kind].fields
        text = (f"{label} wallets saved.\n" + "\n".join(f"{f.label}: `{wallet[f.key]}`" for f in fields)
                + (f"\nUpdated on {updated}." if moved else ""))
    await interaction.followup.send(text, ephemeral=True)


class EvmModal(discord.ui.Modal, title="Raffle wallet (EVM)"):
    """Where the EVM prizes go: any address, a burner is the point. Tickets and the right to enter stay
    with the signed wallets; only the delivery changes. Empty goes back to the primary wallet."""
    # Discord caps a label at 45 characters: a longer one and the modal never opens.
    address = discord.ui.TextInput(label="Prize address (a burner is fine)",
                                   placeholder="0x… · leave empty to go back to your primary wallet",
                                   required=False, max_length=42)

    def __init__(self, bot: KitBot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await save_raffle_wallet(self.bot, interaction, "evm", {"address": self.address.value})


class SolanaModal(discord.ui.Modal, title="Solana wallet for raffles"):
    address = discord.ui.TextInput(label="Solana address", placeholder="Base58, 32-44 characters",
                                   min_length=32, max_length=44)

    def __init__(self, bot: KitBot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await save_raffle_wallet(self.bot, interaction, "solana", {"address": self.address.value})


class KindModal(discord.ui.Modal):
    """A custom wallet kind from config ([[wallet_kinds]]): one input per field, checked by its pattern."""

    def __init__(self, bot: KitBot, kind: str):
        spec = bot.s.wallet_kinds[kind]
        super().__init__(title=f"{spec.label} wallets for raffles"[:45])
        self.bot, self.kind = bot, kind
        self.inputs = {}
        for f in spec.fields:
            self.inputs[f.key] = discord.ui.TextInput(label=f.label[:45], placeholder=f.placeholder[:100] or None,
                                                      max_length=320)
            self.add_item(self.inputs[f.key])

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await save_raffle_wallet(self.bot, interaction, self.kind, {k: v.value for k, v in self.inputs.items()})


def wallet_modal(bot: KitBot, kind: str) -> discord.ui.Modal:
    if kind == "evm":
        return EvmModal(bot)
    if kind == "solana":
        return SolanaModal(bot)
    return KindModal(bot, kind)


class UnlinkSelect(discord.ui.Select):
    def __init__(self, bot: KitBot, wallets: list[Any]):
        super().__init__(placeholder="Unlink a wallet…", options=[
            discord.SelectOption(label=short(w.address), description="EVM" if w.kind == "evm" else "Solana",
                                 value=f"{w.kind}:{w.address}") for w in wallets[:25]])
        self.bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        await unlink(self.bot, interaction, self.values[0])


async def unlink(bot: KitBot, interaction: discord.Interaction, value: str) -> None:
    kind, address = value.split(":", 1)
    uid = str(interaction.user.id)
    await interaction.response.defer(ephemeral=True, thinking=True)
    if not bot.kit.unlink_wallet(uid, kind, address):
        await interaction.followup.send("That wallet is not linked to you any more.", ephemeral=True)
        return
    report = await bot.kit.sync_user(uid, "wallet unlinked")
    await interaction.followup.send(f"Unlinked `{short(address)}`. On-chain now: {report.summary()}.", ephemeral=True)


async def show_wallets(bot: KitBot, interaction: discord.Interaction, refresh: bool = False) -> None:
    """My Wallets and the Change Wallet button on raffle cards."""
    await interaction.response.defer(ephemeral=True, thinking=True)
    kit, s = bot.kit, bot.s
    uid = str(interaction.user.id)
    wallets = kit.store.wallets_of(uid)
    if refresh and wallets:
        await kit.sync_user(uid, "My Wallets refresh")
    e = discord.Embed(title="Wallets", color=s.project.color)
    if not wallets:
        e.description = (f"No wallet linked yet. Press **Link Wallet**, sign once on {site(bot)} and you are set "
                         "— tickets are counted from the NFTs in your wallets automatically.")
        await interaction.followup.send(embed=e, view=LinkView(bot, interaction.user), ephemeral=True)
        return
    lines = []
    for kind in kinds_used(s):
        wallet, own = kit.raffle_wallet(uid, kind)
        if kind == "evm":
            lines.append(f"Raffle wallet (EVM): `{wallet_text(wallet)}`"
                         + (" — your burner, prizes go here" if own else " (primary)"))
        else:
            lines.append(f"{s.kind_label(kind)}: " + (f"`{wallet_text(wallet)}`" if wallet else "not set"))
    lines.append(f"Linked wallets: {len(wallets)}")
    for kind, name in (("evm", "EVM"), ("solana", "Solana")):
        mine = [w for w in wallets if w.kind == kind]
        if mine:
            lines.append(f"{name}: " + ", ".join(short(w.address) + (" (primary)" if i == 0 and kind == "evm" else "")
                                                for i, w in enumerate(mine)))
    h = kit.last_holdings(uid)
    lines.append(f"{s.project.name}: " + (f"holder ×{h.total}" if h and h.total else "not found in linked wallets"))
    tickets, parts = kit.tickets_of(uid)
    explained = ", ".join(parts) or "no NFTs seen yet — press Verify"
    lines.append(f"Raffle tickets: **{tickets}** ({explained})")
    lines.append("")
    lines.append(("**Set Raffle Wallet** — any EVM address the prizes should go to, a burner is fine; tickets "
                  "still come from the NFTs in your linked wallets. " if "evm" in kinds_used(s) else "")
                 + "**Link Another Wallet** signs one more wallet (the NFTs in every linked wallet count). "
                 "To drop one, pick it under **Unlink a wallet**.")
    e.description = "\n".join(lines)[:4096]
    e.set_footer(text="Prizes go to the raffle wallet above; tickets come from your linked wallets.")
    view = LinkView(bot, interaction.user, with_refresh=True, linked=True)
    view.add_item(UnlinkSelect(bot, wallets))
    await interaction.followup.send(embed=e, view=view, ephemeral=True)


async def run_verify(bot: KitBot, interaction: discord.Interaction, why: str) -> None:
    """The Verify button and /nft verify: the link the first time, an instant on-chain check after that."""
    await interaction.response.defer(ephemeral=True, thinking=True)
    kit, s = bot.kit, bot.s
    uid = str(interaction.user.id)
    wallets = kit.store.wallets_of(uid)
    if not wallets:
        await interaction.followup.send(
            f"**Step 1.** Press **Link Wallet** below, sign once on {site(bot)} (free message, not a transaction).\n"
            "**Step 2.** That's it — roles land within seconds, straight from the blockchain. Press **Verify** "
            "again any time to re-check.", view=LinkView(bot, interaction.user, with_kinds=False), ephemeral=True)
        return
    report = await kit.sync_user(uid, why)
    h = report.holdings
    if h is None:
        await interaction.followup.send("Chain RPC is busy - try again in a minute.", ephemeral=True)
        return
    n = h.total
    held = [sp.name for sp in s.specials if h.specials.get(sp.name)]
    tickets, parts = kit.tickets_of(uid)
    text = (f"On-chain right now: **{n} NFT{'' if n == 1 else 's'}**" + (f" + {', '.join(held)}" if held else "")
            + f" across {plural(len(wallets), 'linked wallet')}. Roles updated. Raffle tickets: **{tickets}**"
            + (f" ({', '.join(parts)})" if parts else ""))
    if not n:
        text += ("\nNo NFTs in your linked wallets — if yours sit in another wallet, link THAT wallet too and "
                 "press Verify again.")
    if report.problems:
        text += "\nRoles could not be updated: " + "; ".join(report.problems) + ". Please tell the team."
    await interaction.followup.send(text, ephemeral=True)


class VerifyPanel(discord.ui.View):
    """The persistent panel in the verification channel; survives a restart."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.success, custom_id=VERIFY_ID)
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await run_verify(interaction.client, interaction, "verification panel")  # type: ignore[arg-type]

    @discord.ui.button(label="My Wallets", style=discord.ButtonStyle.secondary, custom_id=WALLETS_ID)
    async def wallets(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await show_wallets(interaction.client, interaction)  # type: ignore[arg-type]
