# Troubleshooting

Start with `python -m kit check` (config and which secrets are set) and the process log. The log
never contains secrets; RPC endpoints appear by host name only.

## Setup commands

**`50001 Missing Access`.** The bot is not in that server (wrong `discord.guild_id`, or the invite was
never completed) or cannot see that channel. Run `python -m kit.setup guilds --wait`; open the invite link
again; check the channel's permissions for the bot.

**`50013 Missing Permissions`.** The bot lacks a permission for what it tried: during `build` usually
Manage Roles, Manage Channels or Manage Server (the server defaults), or a permission a new role or
overwrite hands out (a bot can only grant what it has; the template's Team role carries
Administrator). Invite it with `python -m kit.setup invite-url --admin` for the build;
`python -m kit.setup plan` lists what is missing, and `build` refuses up front instead of failing
halfway.

**"could not register the /nft commands" in the log.** The bot is not in the server in
`discord.guild_id`, or was invited without the `applications.commands` scope. The process keeps
running (panels, ticker, website); open the invite link again, then restart.

**`10004 Unknown Guild`.** `discord.guild_id` is wrong.

**`401` / "Discord rejected DISCORD_TOKEN".** The token was reset or mistyped. Reset it in the
Developer Portal (Bot tab) and update `.env`.

## Roles

**Roles are not given, or "Roles could not be updated: the bot may not manage these roles".**
Two causes. The bot's own role lacks **Manage Roles** (typically after Administrator was unticked
without turning the running permissions on first; channel permissions cannot provide it): give its
role the permissions `python -m kit.setup invite-url` lists. Or role hierarchy: the bot can only give
roles below its own role; in Server Settings > Roles drag the bot's role above Holder, Collector,
Whale, the special roles and the self-roles. `kit.setup plan` notes both problems.

**"the role 'X' does not exist on the server".** A role named in `config.toml` was never created or
was renamed. Run `python -m kit.setup build` (it creates missing roles), or fix the name in the config.

**Roles did not change although someone sold.** If the chain could not be read, roles are left
alone on purpose ("unknown is not zero"); the next sync fixes them. `/nft check` shows `?` per wallet
when the node did not answer.

## The bot does not start

**"The bot's SERVER MEMBERS INTENT is off".** Run `python -m kit.setup app` (it turns the intents on
through the API), then restart. If Discord refuses that (a verified app needs approval), Developer
Portal > Bot > Privileged Gateway Intents > Server Members Intent, save, restart. `python -m kit check`
shows the intents' state. The bot needs it to see joins and read roles.

**`kit.setup token --from-clipboard` cannot read the clipboard.** It needs a desktop session on the
machine the kit runs on (over SSH there is none) and Python's tkinter (Debian/Ubuntu:
`apt install python3-tk`). Otherwise the person pastes the token into `.env` as `DISCORD_TOKEN=`.

**Slash commands missing.** The process must be running; the bot registers `/nft` for the configured
server at start. If they still do not appear, it was invited without the `applications.commands`
scope: open the invite link again (no need to remove the bot first).

## Buttons and commands

**"This interaction failed".** Discord got no answer within 3 seconds. Usually the process is down or
restarting (check `/healthz` and the log), or the panel was posted by another bot. Panels posted by
this kit keep working across restarts; re-run `python -m kit.setup panels` if one was deleted.

**"Something went wrong: ...".** An unexpected error; the full traceback is in the log.

**The giveaway alert role is not tagged on new cards.** The role is not mentionable and the bot lacks
"Mention @everyone, @here and All Roles" in that channel. The template grants it in the giveaways
channel; for another channel add that permission for the bot, or make the role mentionable (then
anyone can ping it). The log says "giveaway tag skipped in #channel".

**The winners were not announced.** The log says why: no channel to announce in (`[channels]
winners` does not match a channel and the raffle's own channel is gone), or the bot may not post there.
The raffle stays open, undrawn, and the bot tries again after 1 minute, then 2, 4, ... up to once an
hour. Fix `[channels] winners` or the channel permissions (View, Send Messages, Embed Links).

**Ticket transcripts are empty.** Turn on **MESSAGE CONTENT INTENT** in the Developer Portal (Bot
tab); without it Discord sends the bot other people's messages with no text. The log says so too.

## Chain reads

**"Chain RPC is busy - try again in a minute." / `rpc ... HTTP 429` in the log.** The RPC endpoint is
rate-limiting. Use a keyed endpoint (Alchemy, Infura, QuickNode, a node of your own) and list a
second URL as a fallback: `RPC_ETHEREUM=https://primary,...,https://fallback`.

**`multicall unusable here`.** That chain has no Multicall3 at the usual address; the kit falls back
to one `eth_call` per read. Set `multicall = ""` on that collection to skip the attempt.

**`kit check` warns "nothing is deployed at 0x... on the RPC_X chain".** The contract address is wrong,
or `RPC_X` points at another chain. Until it is fixed every reading for that collection stays
"unknown" (roles are left alone and Enter answers "Chain RPC is busy"), never "zero".

**Solana holdings are always 0.** The RPC must support the DAS API (`getAssetsByOwner`); plain
Solana RPC endpoints do not. The collection address must be the verified collection, and
`[verification] solana = true`.

## The verify page

**"No wallet extension found".** On a computer, install MetaMask, Rabby, Phantom or another wallet
extension. On a phone, use the "Open in ..." links: they reopen the page inside the wallet's browser.

**The link opens inside Discord and nothing works.** Discord's in-app browser has no wallet. Use the
menu (⋯) > Open in browser, or a wallet's "Open in ..." link.

**"The signature does not match this wallet".** The wallet signed with a different account than the
one connected. Switch the account in the wallet, press Connect again, then Sign.

**"Link expired".** Links last 30 minutes and each signing request 10 minutes; press Verify in Discord
for a new link.

**Wallet linked, page says "join the Discord server".** The account is not in the server (or the bot
cannot see it yet). Join the server; the roles are given on joining.

## Hosting

**Everything resets after a deploy.** `data/` is not on a persistent disk: mount a volume at `/data`
(Docker, Railway, Render, Fly.io; see `references/hosting.md`).

**`sqlite3.OperationalError: database is locked`.** Two instances share one database. Run exactly one.

**Two answers to every button.** Two instances are running (an old one never stopped). Stop all but one.
