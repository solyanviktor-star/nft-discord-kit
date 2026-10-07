# config.toml, every key

`config.example.toml` is a commented copy of everything below. `python -m kit check` validates the
whole file at once: wrong types, unknown keys (typos), bad addresses, the example's placeholder
contract, broken regular expressions, missing references between sections. It then asks each EVM
collection's RPC whether code exists at the contract and warns when nothing is deployed there (a
wrong address, or an RPC of another chain) or the RPC does not answer; `--offline` skips that.
Secrets never go in this file; see "Environment" at the end.

## [project]

| Key | Default | Meaning |
|---|---|---|
| `name` | required | Project name, used in embeds, the site and replies ("No *Name* NFT found ..."). |
| `color` | `"#5865F2"` | Accent colour of embeds and the website. |
| `logo_url` | `""` | Optional https image for the website header (otherwise the first letter of the name). |
| `public_url` | `""` | Where the website is served, `https://verify.example.com` (no path). `PUBLIC_URL` in the environment wins. Required for `run`. `http://localhost:PORT` is allowed for local tests. |

## [discord]

| Key | Default | Meaning |
|---|---|---|
| `guild_id` | `0` | The server id (`python -m kit.setup guilds`). Number or string. Required for `run` and the setup commands that touch a server. |
| `application_id` | `0` | Only if reading it from the token is blocked; the OAuth client id. |
| `staff_roles` | `["Team", "Mod"]` | A member is staff when a role name **starts with** one of these, or they have Manage Server. Staff run every team command. |
| `raffle_manager_roles` | `["Raffle Manager"]` | These roles (by prefix) may run the raffle commands only. |
| `raffle_users` | `[]` | Discord user ids allowed to run the raffle commands only (an assistant without a team role). |

## [channels]

Which channels the bot uses. A value is a channel name (case and leading emojis ignored) or a numeric
id. After `kit.setup build`, the ids saved in `data/state.json` are preferred.

| Key | Default | Used for |
|---|---|---|
| `verify` | `"verify"` | The verify panel; mentioned in "Only holders can enter. Verify in #verify ...". |
| `giveaways` | `"giveaways"` | Default channel for raffle cards. |
| `winners` | `"giveaways-winners"` | Winner announcements (falls back to the card's channel). |
| `tickets` | `"tickets"` | The ticket panel; private ticket threads open here. |
| `ticket_log` | `"ticket-log"` | Ticket opened/closed log with transcripts. |
| `self_roles` | `"claim-roles"` | The self-roles panel. |

## [web]

| Key | Default | Meaning |
|---|---|---|
| `host` | `""` | `""` listens on all interfaces (Docker, PaaS). Use `"localhost"` behind a proxy on the same machine. |
| `port` | `8080` | `PORT` in the environment wins (Railway, Render and Fly.io set it). |
| `trust_proxy` | `true` | Take the client IP from the last `X-Forwarded-For` hop (the one Caddy or the platform added), but only for requests that come from a loopback or private address, i.e. from a proxy. Requests straight from the internet are always counted by their own address. `false` ignores the header entirely. |
| `rate_limit_per_minute` | `30` | Requests per IP per minute on `/api/*` and `/auth/*`. |

## [verification]

| Key | Default | Meaning |
|---|---|---|
| `sync_minutes` | `30` | Every linked member is re-read and their roles fixed this often (and once at start). |
| `chain_id` | `1` | The Chain ID line of the EVM sign-in message (any chain works for signing). |
| `solana` | `false` | Also link Solana wallets (Phantom, Solflare, Backpack). Required for Solana collections. |

## [[collections]] (at least one)

| Key | Default | Meaning |
|---|---|---|
| `name` | required | Unique name; specials refer to it. |
| `chain` | required for EVM | Any label (`ethereum`, `base`, ...); it names the default RPC variable. |
| `standard` | `"erc721"` | `erc721` (balanceOf), `erc1155` (balanceOf per id in `token_ids`), or `solana` (DAS). |
| `contract` | required | EVM: the 0x contract. Solana: the verified collection address. |
| `rpc_env` | `RPC_<CHAIN>` | The environment variable holding this chain's RPC URL(s), comma separated, tried in order. Solana needs a DAS-capable RPC (`getAssetsByOwner`). |
| `token_ids` | `[]` | ERC-1155 only: the token ids that count. |
| `role` | `""` | Optional role for anyone holding at least one token of this collection. |
| `multicall` | Multicall3 address | Reads are batched through Multicall3 at `0xcA11bde05977b3631167028862bE2a173976CA11`. Set `""` for a chain without it (plain `eth_call` is used; it also falls back automatically). |

Holdings add up across all collections and all of a member's linked wallets.

## [[tiers]]

Default: Holder 1+, Collector 3+, Whale 10+. Each entry has `name` and `min` (1 or more, all different).
Tiers stack: a member wears every tier whose `min` they reach. The lowest tier is what the template
calls "holders" for channel access.

## [[special]] (optional)

| Key | Meaning |
|---|---|
| `name` | Label shown in tickets and replies ("Legendary +3"). |
| `role` | Role given while one of these tokens is held. |
| `collection` | An EVM collection `name` above. ERC-721: ownership read with `ownerOf`; ERC-1155: `balanceOf` per id. |
| `token_ids` | The token ids, inline. |
| `token_file` | Or a file (relative to config.toml) with ids separated by spaces, commas or new lines; `#` comments allowed. |
| `bonus_tickets` | Extra raffle tickets while held (default 0). |

If the chain does not answer for the special tokens in a round, those roles are left alone until it does.
Role names must be unique across tiers, collection roles and specials.

## [raffles]

| Key | Default | Meaning |
|---|---|---|
| `require_holding` | `true` | `false`: linked members without NFTs may enter and get 1 ticket. |
| `tickets_per_token` | `1` | Tickets per NFT held. |
| `ticket_cap` | `10` | NFTs counted per account (ten NFTs in one account count the same as ten split over ten accounts). |
| `special_bonus` | `"best"` | `"best"`: only the largest special bonus held; `"sum"`: every special held adds its bonus. |
| `alert_role` | `"Giveaway Alerts"` | Tagged on every new raffle card; `""` for no tag. |
| `eligible_roles` | `[]` | Default eligible roles for new raffles (by name). Empty: every holder role (or everyone, when `require_holding = false`). |

## [[raffle_chains]]

The choices of `/nft raffle create chain:` (1 to 25). Default: one entry, Ethereum / evm.

| Key | Meaning |
|---|---|
| `name` | Shown on the card ("Chain"). |
| `wallet_kind` | `evm` (the last verified EVM wallet, or a prize address set with **Set Raffle Wallet**), `solana` (typed in a modal, or the verified Solana wallet), or the `id` of a `[[wallet_kinds]]` entry. |

## [[wallet_kinds]] (optional custom kinds)

For prize chains that need other addresses. Each entry: `id` (lower case, not evm/solana), `label`,
and `fields`: 1 to 5 of `{ key, label (45 characters at most), pattern (a regular expression the whole
value must match), placeholder }`. Members fill them in a modal; the CSV export gets one
`wallet_<key>` column per field.

## [support]

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Support tickets on or off. |
| `staff_roles` | `discord.staff_roles` | Who is pinged in new tickets, sees and closes them (by prefix). |
| `categories` | Prize, Support, Partnership, Other | 1 to 5 `{ emoji, name }`: one panel button each. |
| `max_open` | `1` | Open tickets per person (staff are not limited). |
| `archive_hours` | `72` | Idle ticket threads auto-archive after the nearest Discord step: 1 h, 24 h, 3 days or 7 days. |
| `greeting` | "Thanks! Describe your question below ..." | Posted in each new ticket as "**Project:** greeting". |

## [self_roles]

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | The self-roles panel. |
| `roles` | Giveaway Alerts | Up to 20 `{ name, emoji, description }`; a button each toggles the role. |

## [oauth]

The Discord login on the website turns on when `DISCORD_CLIENT_SECRET` is set.

| Key | Default | Meaning |
|---|---|---|
| `auto_join` | `false` | Also ask for `guilds.join`; after verifying, someone not yet in the server is added with their roles. |

## [web_raffles]

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Raffles on the website (`/raffles`, `/api/raffles`, `embed.js`). Needs the OAuth login. |
| `recent_days` | `7` | How long ended raffles stay listed. |

## Environment (.env)

| Variable | Meaning |
|---|---|
| `DISCORD_TOKEN` | The bot token. Required. |
| `DISCORD_CLIENT_SECRET` | OAuth client secret; turns on the website login. |
| `SESSION_SECRET` | Signs verify links and login cookies: 32 or more random characters (shorter is refused by `kit check` and `run`). Empty: generated into `data/session_secret` and kept, so on a host without a persistent disk set it here. Changing it invalidates open links and logins. |
| `PUBLIC_URL` | Overrides `project.public_url`. |
| `PORT` | Overrides `web.port`. |
| `RPC_*` | RPC URLs, named by each collection's `rpc_env`. Never logged (only host names are). |
| `KIT_DATA_DIR` | Where `kit.db`, `state.json`, the session secret and ticket transcripts live (default `data`). |
| `KIT_CONFIG` | Config path (default `config.toml`). |

Real environment variables win over `.env`.
