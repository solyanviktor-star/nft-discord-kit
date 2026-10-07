# Raffles (giveaways)

## Who may run them

- **Staff**: Manage Server permission, or a role starting with a name in `discord.staff_roles`.
- **Raffle runners only**: roles in `discord.raffle_manager_roles`, and user ids in `discord.raffle_users`.
  They get the `/nft raffle ...` commands and nothing else of the team's.

Anyone else gets "Team only.".

## Commands

| Command | What it does |
|---|---|
| `/nft raffle create title link chain duration gtd [fcfs] [description] [image] [roles] [channel]` | Posts a card. `duration`: `24h`, `2d 12h`, `90m`, `1w` (at least a minute). `gtd` + `fcfs` at least 1. `roles`: mention the eligible roles; default `raffles.eligible_roles`, else every holder role. `channel`: default the giveaways channel. |
| `/nft raffle end raffle` | Ends an open raffle now and draws. |
| `/nft raffle reroll raffle [count]` | Draws `count` (default 1) more winners for an ended raffle; earlier winners are excluded; new winners join the GTD list. |
| `/nft raffle export raffle [everyone]` | CSV of the winners (type, discord_id, discord_name, wallet columns), or of every entrant (discord_id, discord_name, wallet columns, tickets). A raffle not drawn yet always exports its entrants. |
| `/nft raffle list` | The 25 newest raffles with id, chain, status, entrants and deadline. |
| `/nft raffle cancel raffle` | Cancels an open raffle without drawing. |
| `/nft raffle panel` | Posts the verify panel in the current channel. |
| `/nft giveaways` | Anyone: open raffles, and whether you are in with how many tickets. |
| `/nft me` | Anyone: raffle wallets, holdings, tickets and raffles won. |
| `/nft check user` | Staff: a member's wallets with per-wallet holdings read now, roles, entries and wins. |

`raffle` options autocomplete by id or by part of the title (Discord shows 25 choices), newest first.

## The card

Title (with `[ENDED] ` once drawn or cancelled, grey), the link and the description, then Ends (date
and countdown), Chain, Winners, Entrants, Guaranteed, FCFS, Eligible Roles and how tickets are counted;
the optional image. Buttons: **Enter** and **Change Wallet**. The card's message tags the alert role
(`raffles.alert_role`, default Giveaway Alerts, a self-role). A role that is not mentionable needs the
bot to have "Mention @everyone, @here and All Roles" in that channel (the template grants it in the
giveaways channel); without it the card goes out untagged and the log says why.

Cards are redrawn on a schedule, not on every entry: a card younger than an hour at most every 3
minutes, an older one at most every 15 (Discord rate-limits edits of older messages). Ending or
cancelling redraws at once. If someone deletes a card, it is not redrawn again; the raffle itself
still runs and draws.

## Entering

**Enter** checks, in this order, and answers privately:

| Situation | Reply |
|---|---|
| raffle ended or cancelled | "This raffle has ended." |
| past the deadline or being drawn | "This raffle is closing right now." |
| no eligible role | "Only holders can enter. Verify in #verify to get your role - it is automatic after that." |
| no wallet linked | "Link your wallet once and you are in for every raffle after that." + a Link Wallet button |
| the chain cannot be read and there is no earlier reading | "Chain RPC is busy - try again in a minute." |
| no NFT held (with `require_holding`) | "No *Project* NFT found in your linked wallets - if yours sits in another wallet, link THAT wallet too and press Verify." |
| no wallet of the raffle's kind | EVM: "No EVM wallet linked - link one first."; others: "This raffle is on *Chain* - add your *Kind* wallet first." + buttons |
| first entry | "You're in! **N tickets** (labels) · wallet `address`" |
| pressed again | "You're already in ✅ · **N tickets** (labels) · wallet `address`" (tickets and wallet refreshed) |

Tickets are read from the chain at that moment. The website entry (optional) goes through the very
same function.

## Wallet kinds

Each raffle chain names a wallet kind (`[[raffle_chains]]`):

- `evm`: the most recently verified EVM wallet, or a **prize address** set with **Set Raffle Wallet**
  (any address, a burner is fine; empty goes back to the verified one). Tickets still come from the
  linked, signed wallets.
- `solana`: an address typed in a modal, or the verified Solana wallet.
- custom kinds (`[[wallet_kinds]]`): one or more fields, each checked against its pattern.

Changing a raffle wallet also updates the person's entries in open raffles of that kind (the prize goes
to the address on the entry, and "I changed it, why did it go to the old one" must not happen).
Setting a raffle wallet needs a linked wallet first.

## Tickets

`tickets_per_token` × min(NFTs held, `ticket_cap`) + the special bonus: the largest bonus held
(`special_bonus = "best"`) or all of them (`"sum"`). Example with the defaults and a Legendary
(+3): 14 NFTs and a Legendary = 10 + 3 = 13 tickets, shown as "14 NFTs (max 10), Legendary +3".
With `require_holding = false`, linked members without NFTs enter with 1 ticket.

## The draw

Every 30 seconds the bot draws the raffles past their deadline. While a raffle is being drawn it takes
no entries (from the button or the website), so nobody slips in between the recount and the pick.

1. Recount: all entrants' wallets are read in one batch. Someone who sold drops out (0 tickets).
   Someone the node cannot read keeps the tickets of their last reading, or what they entered with.
2. Weighted pick without repeats (`random.SystemRandom`): the chance of each pick is proportional to
   tickets. The first `gtd` picks are GTD, the next `fcfs` are FCFS.
3. The winners message goes to the winners channel: the mentions in messages under Discord's 2000-
   character limit (several messages for a long list), an embed with the GTD and FCFS lists (counts
   only when the list is too long for an embed), and a **View Giveaway** button back to the card.
4. The raffle is marked ended with the winners and their wallets saved; the card turns `[ENDED]`.

If the winners cannot be announced (no winners channel and the raffle's channel is gone, or the bot
may not write there), nothing is drawn yet: the raffle stays open, the log says why, and the bot
tries again after a minute, then after 2, 4, ... up to once an hour, until it works. Other raffles are
not held up. `/nft raffle end` shows the same reason right away.

## Data

Raffles, entries and results live in SQLite (`data/kit.db`). Entries keep the Discord id and name,
the wallet (as entered), the tickets at entry time and the entry time.
