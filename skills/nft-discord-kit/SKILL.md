---
name: nft-discord-kit
description: Builds a holder-gated Discord server for an NFT project and runs it with one self-hosted bot - server layout (roles, categories, channels, permissions), a wallet verification website (EVM, optional Solana; one signed message; holder roles synced from the chain), raffles/giveaways with on-chain tickets, support tickets in private threads, self-roles, and optionally raffle entry from the project's own website. Use when someone asks to "set up our Discord", "make a holder-gated or token-gated Discord", "verify NFT holders in Discord", "give holder roles from the blockchain", "run NFT giveaways or raffles in Discord", "add wallet verification to our server", "replace our third-party verification bot", or "add support tickets to our NFT Discord".
---

# nft-discord-kit

You are setting up a Discord server and a bot for an NFT project, together with the person who owns
it. Everything ships in `app/` next to this file: a Python 3.11+ application (`kit/`), a server layout
template, deploy files and tests. One process (`python -m kit run`) runs the Discord bot, the
verification website and SQLite storage.

What the person gets:
- A server laid out for a holder community: a public verification channel; holder-only categories
  (announcements, giveaways, holder chat, community, product updates, claim-roles, tickets); a
  team-only log; staff roles (Team, Mod, Raffle Manager, Collab Manager); holder tier roles.
- Holder login: **Verify** hands out a personal link; the person connects a wallet on the project's
  site, signs one free message, and the bot gives roles from on-chain holdings at once. Roles stay in
  sync on a timer.
- Raffles: `/nft raffle create ...` posts a card with **Enter** and **Change Wallet**; tickets come from
  the NFTs held (capped, plus special-token bonuses); the bot draws at the deadline, recounting on-chain.
- Support tickets (private threads, transcripts to a log channel) and a self-roles panel.
- Optional last stage: entering raffles from the project's own website.

Read a reference file only when a step needs it: `references/discord-app.md`,
`references/server-template.md`, `references/config.md`, `references/verification.md`,
`references/raffles.md`, `references/tickets-and-roles.md`, `references/hosting.md`,
`references/website-raffles.md`, `references/troubleshooting.md`.

## Guardrails (always)

- **Never print, echo, log or commit a secret**: the bot token, the OAuth client secret, the session
  secret, keyed RPC URLs. Do not `cat .env`, do not repeat its values, do not put secrets in
  `config.toml`, commit messages or chat. `python -m kit check` shows only whether each secret is set.
- Secrets live in `.env` only (gitignored; `.env.example` is the template). Ask the person to paste
  the token into `.env` themselves (open the file for them). If they paste a secret into the chat
  anyway, write it to `.env` without repeating it, and tell them they can reset it later if the chat
  log is shared with anyone.
- Before changing a server that already has channels of its own, show `python -m kit.setup plan`
  (read-only) and get an explicit yes. The builder only creates: it never deletes, renames or moves
  anything, and never edits an existing permission overwrite (it only adds missing ones).
- Do not invent ids. Discord ids come from `python -m kit.setup guilds`, the person, or `data/state.json`.
- Work in the copy of `app/` inside the person's project, never in this skill folder.

## Step 0: interview (one message, with defaults)

Ask everything at once and offer the defaults in brackets, so "defaults are fine" is a valid answer:

1. Project name, accent colour [#5865F2], logo URL [none].
2. Collections: for each, chain name, contract address, standard (`erc721` [default], or `erc1155`
   with the token ids that count), an optional own role. Solana collections need a DAS-capable RPC.
3. Holder tiers by total NFTs held [Holder 1+, Collector 3+, Whale 10+].
4. Special token sets, if any (1/1s, rarity tiers): name, role, token ids, bonus raffle tickets [none].
5. Raffle chains offered in giveaways and the wallet each one needs [Ethereum: EVM, Solana: Solana];
   custom kinds with their own fields if a chain needs them (e.g. Bitcoin Ordinals: two addresses).
6. Ticket rules [1 ticket per NFT, counted up to 10, special bonus: the best one]; must entrants hold
   an NFT [yes]?
7. Staff role names [Team, Mod], raffle managers [Raffle Manager], people who may run raffles only
   (Discord user ids) [none].
8. Modules: support tickets [on: Prize, Support, Partnership, Other], self-roles [on: Giveaway Alerts],
   Solana wallet linking [off].
9. Hosting: where it will run (their VPS with Docker, a VPS without Docker, Railway, Render, Fly.io,
   other) and the domain for the verification site (e.g. `verify.theirproject.xyz`).

Do not ask about raffles on their website yet; that is step 8.

## Step 1: Discord application, bot, and the app

Copy and install the app first, so `.env` exists:

```bash
cp -r <this skill folder>/app ./discord-kit && cd discord-kit
cp .env.example .env
python3 -m venv .venv && . .venv/bin/activate   # Windows: py -3.11 -m venv .venv; .venv\Scripts\activate
pip install -r requirements-dev.txt
python -m pytest -q                              # must pass before anything touches Discord
```

Then the person clicks through https://discord.com/developers/applications (details:
`references/discord-app.md`):
1. **New Application**, name it.
2. **Bot** tab: **Reset Token**, copy it, paste it into `.env` as `DISCORD_TOKEN=` (they do it).
   Turn **Public Bot** off. Under **Privileged Gateway Intents** turn on **SERVER MEMBERS INTENT**
   (roles, joins) and **MESSAGE CONTENT INTENT** (without it ticket transcripts come out empty).
3. Only if they want "Verify with Discord" on their site (needed for auto-join and for website
   raffles): **OAuth2** tab, add the redirect `https://<their domain>/auth/discord/callback`, reset
   the **Client Secret** and paste it into `.env` as `DISCORD_CLIENT_SECRET=`.

RPC URLs go into `.env` under the env names `config.toml` uses (`RPC_ETHEREUM=...`; several URLs
separated by commas are tried in order). A public endpoint works for small servers; a keyed one
(Alchemy, Infura, QuickNode, ...) avoids rate limits. Never print these URLs: keys hide in them.

## Step 2: the server

1. The person creates an empty server: Discord, **+** (Add a Server), **Create My Own**, **For me and
   my friends**, a name. An existing server works too (then plan carefully, see the guardrails).
2. `cp config.example.toml config.toml` and set `project.name` (`guild_id` stays 0 for now).
3. `python -m kit.setup invite-url --admin` prints the link that adds the bot with Administrator,
   which the build needs (the template's Team role carries Administrator, and a bot can only hand out
   permissions it has). The person opens it, picks the server, authorizes. Without `--admin` the link
   asks only for what the running bot needs; it is for a server whose layout is built already.
4. `python -m kit.setup guilds` lists the servers the bot is in. Put the id into `discord.guild_id`.

## Step 3: config

Write `config.toml` from the interview answers (every key: `references/config.md`), keeping the
comments. Run `python -m kit check`: it reports every problem at once (unknown keys included, and the
example's placeholder contract), prints a summary without secrets, and asks each collection's RPC
whether the contract exists ("nothing is deployed at ..." means a wrong address or an RPC of another
chain: fix it, or every reading stays unknown). Fix everything it lists; show the person the summary.

## Step 4: plan, then build

1. `python -m kit.setup plan` is read-only (GET requests only). Show the person the list: roles,
   categories, channels, permission overwrites to add, server settings, plus any notes (a role that
   sits above the bot, a channel that already exists elsewhere, permissions the bot's role lacks).
2. Adjust names and emojis in `templates/server.toml` if they want (`references/server-template.md`).
   If they rename a channel the bot uses, update `[channels]` in `config.toml` to match.
3. After an explicit yes: `python -m kit.setup build --yes`. It creates only what is missing and
   saves the ids to `data/state.json`. Running it again is safe: it finds everything and adds nothing.
   It refuses up front (changing nothing) if the bot lacks a permission the plan needs.
4. Discord's default `general` channels stay; the person may delete them by hand.
5. Optional hardening once built (do not simply untick Administrator: Manage Roles cannot come from
   channel permissions, and without it no holder role or self-role can be given). In Server Settings >
   Roles > the bot's own role, first turn on exactly the permissions
   `python -m kit.setup invite-url` lists under "asks for": View Channels, Send Messages, Embed Links,
   Attach Files, Read Message History, Mention @everyone, @here and All Roles, Manage Roles, Manage
   Threads, Create Private Threads, Send Messages in Threads (plus Create Invite with
   `oauth.auto_join`); then turn off Administrator. Keep the bot's role above the holder, tier,
   special and self-roles. Run `python -m kit.setup plan` again: it notes any permission still missing.

## Step 5: deploy

Follow `references/hosting.md` for the chosen host. Needed: a long-running process (the bot holds a
gateway connection; serverless functions cannot), HTTPS on their domain, and a persistent disk for
`data/` (SQLite and the session secret). Set `PUBLIC_URL=https://<domain>`. Check that
`https://<domain>/healthz` answers `{"ok": true, "discord": true}`.

## Step 6: panels

`python -m kit.setup panels` posts the verify panel (verification channel), the ticket panel (tickets
channel) and the self-roles panel (claim-roles channel); later runs refresh them in place. From
Discord, `/nft raffle panel` and `/nft tickets panel` post the same panels in the current channel.

## Step 7: smoke test together

With the person, in their server:
1. A holder presses **Verify**, opens the link, connects the wallet, signs. The page says e.g.
   "3 NFTs found - roles Collector, Holder given" and the roles appear.
2. In the giveaways channel: `/nft raffle create` with `title: Test`, `link: https://example.com`,
   `chain: Ethereum`, `duration: 2m`, `gtd: 1`. Enter with the holder ("You're in! **N tickets** ...").
3. At the deadline the bot draws by itself within 30 seconds, posts the winners and marks the card
   [ENDED].
4. `/nft raffle export` for that raffle returns a CSV of the winners' wallets.
5. Open a support ticket from the ticket panel and close it; the transcript lands in the log channel.

Problems: `references/troubleshooting.md`.

## Step 8: ask about website raffles (separate, optional)

Only now ask: "Do you also want holders to enter raffles from your website?" If yes, follow
`references/website-raffles.md` (it needs the Discord login from step 1.3): `[web_raffles]
enabled = true`, restart, and optionally put the `embed.js` snippet on their site. If no, leave it off.

## Day-to-day (for the person)

- Everyone: **Verify** / **My Wallets** on the panel, `/nft verify`, `/nft me`, `/nft giveaways`.
- Raffle runners: `/nft raffle create | end | reroll | export | list | cancel | panel`.
- Staff: `/nft check`, `/nft tickets panel`, `/nft tickets close [note]` (the author may close too).
- Holder roles re-sync every `verification.sync_minutes` (30), right after linking and on joining.
- Updating: copy the new `app/` over theirs except `config.toml`, `.env` and `data/`, run
  `python -m kit check` and the tests, restart. Back up `data/kit.db` (and keep `.env` safe).
