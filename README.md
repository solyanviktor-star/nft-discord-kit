# nft-discord-kit

An Agent Skill (and a Claude Code plugin) that sets up a holder-gated Discord server for any NFT
project, and the self-hosted bot that runs it. Ask your agent "set up our Discord" and it walks you
through everything, from creating the bot to a test raffle.

## What you get

- **A server layout**: a public verification channel; holder-only categories for announcements,
  giveaways and winners, holder chat and alpha, community (art, memes, raids, share-your-profit),
  product updates and suggestions, claim-roles and tickets; a team-only log; staff roles (Team, Mod,
  Raffle Manager, Collab Manager) and holder tier roles. Built by a command that only ever creates
  what is missing, after a read-only dry run.
- **Holder verification on your own site**: members press **Verify** in Discord, connect a wallet on
  your verification page (EVM wallets through EIP-6963 or `window.ethereum`, optionally Phantom,
  Solflare, Backpack), sign one free message, and get their roles within seconds. Holdings are read
  on-chain (ERC-721, ERC-1155, special token sets, optional Solana collections) and roles stay in sync
  on a timer. Contract wallets work through EIP-1271.
- **Raffles / giveaways**: `/nft raffle create` posts a card with **Enter** and **Change Wallet**;
  tickets come from NFTs held (capped per account, plus bonuses for special tokens); the bot draws
  at the deadline after recounting everyone on-chain, announces GTD and FCFS winners, and exports
  winners' wallets as CSV. Prize wallets per chain: EVM, Solana, or custom kinds with their own fields.
- **Support tickets** in private threads, with the holder's context, staff pings and transcripts.
- **Self-roles** (e.g. Giveaway Alerts, tagged on every new raffle).
- **Optional**: entering raffles from your own website, plus an embeddable list.

One Python process runs the Discord bot, the verification website (aiohttp) and SQLite storage.
Deploy it on any host that runs a long-lived container with HTTPS: a VPS with Docker or systemd and
Caddy, Railway, Render, Fly.io.

## Install

**As a Claude Code plugin** (from a clone or from GitHub):

```bash
claude plugin marketplace add solyanviktor-star/nft-discord-kit   # or a local path to this repo
claude plugin install nft-discord-kit@nft-discord-kit
```

**As a plain Agent Skill** (Claude Code or any agent that reads `SKILL.md` skills): copy
`skills/nft-discord-kit/` into the agent's skills folder, e.g. `~/.claude/skills/nft-discord-kit/`
(personal) or `.claude/skills/nft-discord-kit/` (one project). The folder is self-contained: the
playbook, the reference docs and the application all live inside it.

Then ask your agent: *"Set up a holder-gated Discord for our NFT collection."*

## What the agent does with you

0. Asks about your project in one go (collections, tiers, raffle chains, staff, modules, hosting),
   with sensible defaults.
1. Has you create the Discord application and press Copy on its token; saves it into `.env` from
   the clipboard (it never passes through the chat), then turns on the bot's intents and sets its
   description and icon through Discord's API.
2. Has you create an empty server and approve the bot's invite; picks up the server id by itself.
   Only these three steps need your Discord account. You click them (about three minutes), or, if the
   agent has a browser tool and you opt in, it clicks them for you (Discord's rules forbid automating
   user accounts, so that mode is at your own risk).
3. Writes `config.toml` and validates it.
4. Shows a read-only plan of the server layout, then builds it after your yes.
5. Deploys to the host you picked, on your domain with HTTPS.
6. Posts the verify, ticket and self-roles panels.
7. Runs a smoke test with you: verify a wallet, a two-minute test raffle, the automatic draw, export.
8. Only then asks whether you also want raffles on your website.

Secrets stay in `.env` (gitignored); the playbook tells the agent never to print or commit them.

## Repository layout

```
.claude-plugin/           plugin.json and marketplace.json (one plugin, source "./")
skills/nft-discord-kit/
  SKILL.md                the agent's playbook
  references/             loaded on demand: discord-app, browser-setup, server-template, config,
                          verification, raffles, tickets-and-roles, hosting, website-raffles,
                          troubleshooting
  app/                    the application the agent copies into your project
    kit/                  the Python package (pure logic modules, kit/bot = Discord, kit/web = site)
    templates/server.toml the server layout
    config.example.toml   every setting, commented
    .env.example          the secrets it needs
    Dockerfile, docker-compose.yml, deploy/ (systemd unit, Caddyfile)
    tests/                pytest suite
```

## Run the tests

```bash
cd skills/nft-discord-kit/app
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python -m pytest -q
```

The suite covers the pure logic (signatures, state tokens, nonces, tickets, the draw, the template
planner, config validation), the chain reader against a fake JSON-RPC node (Multicall3 and fallbacks),
the website flow (nonce, real signatures, linking, roles), the setup commands against a fake Discord
API (including that `plan` only reads), and the Discord layer driven with stand-in objects.

## License

MIT, see `LICENSE`.
