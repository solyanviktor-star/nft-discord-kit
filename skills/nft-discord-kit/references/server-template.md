# The server template

`templates/server.toml` describes the server; `python -m kit.setup plan` compares it with the real
server and `python -m kit.setup build` creates what is missing.

## What the default layout contains

| Category | Access | Channels |
|---|---|---|
| ✅ Verification | public, read-only | ✅┃verify (the verify panel) |
| 📌 Important | holders, read-only | 📢┃announcements, 🔗┃official-links |
| 🎁 Giveaways | holders, read-only | 🎁┃giveaways (raffle cards), 🏆┃giveaways-winners |
| 💎 Holders | holders | 💬┃holder-chat, 🔥┃alpha-chat, 🎙┃stage (voice) |
| 🎨 Community | holders | 🖌┃community-art, 😂┃memes, 🐦┃raids, 💰┃share-your-profit |
| 🛠 Product | holders | 🛠┃development-updates (read-only), 💡┃suggestions |
| 🔔 Notifications | holders, read-only | 🎭┃claim-roles (the self-roles panel) |
| 📬 Tickets | holders, read-only | 📩┃tickets (ticket panel; people write inside their private thread) |
| 🔒 Team | staff | 📋┃ticket-log (ticket transcripts) |

Roles, top to bottom: Team (Administrator), Mod (manage messages and threads, kick, ban, timeout),
Raffle Manager, Collab Manager (may mention everyone), then from `config.toml`: special roles, the
holder tiers (highest first), collection roles, self-roles and the giveaway alert role.

## Access presets

Set per category with `access = "..."`; a channel may override it with its own `access`.

| Preset | Who sees | Who writes |
|---|---|---|
| `public` | everyone | everyone |
| `public_readonly` | everyone | staff roles and the bot |
| `holders` | holders and staff | holders and staff |
| `holders_readonly` | holders and staff | staff roles and the bot |
| `staff` | staff roles and the bot | staff roles and the bot |

- "Holders" means the **lowest tier role** (tiers stack: someone with 10 NFTs wears Holder, Collector
  and Whale), plus template roles with `access = "holders"` (Raffle Manager, Collab Manager).
  A channel may list `roles = ["Whale"]` to be seen only by those roles (and staff), e.g. a whale lounge.
- "Staff" means template roles with `access = "staff"` (Team, Mod).
- The bot gets its own member overwrite in every non-public text channel: View, Send, Embed Links,
  Attach Files, Read History. `bot = [...]` adds more: the giveaways channel adds `mention_everyone`
  (for the alert tag), the tickets channel adds `create_private_threads`, `manage_threads`,
  `send_messages_in_threads` and `mention_everyone`.
- `allow_threads = true` (tickets) keeps "Send Messages in Threads" open for members, so people can
  write inside the private ticket thread they were added to, while the channel itself stays read-only.
- Voice channels: holders get View + Connect + Speak; there is no bot overwrite.

Permission names are Discord's in snake case: `view_channel`, `send_messages`, `manage_threads`,
`mention_everyone`, `administrator`, ... (the full list is `PERMISSIONS` in `kit/template.py`).

## Editing

- Rename anything, change emojis, add or remove channels and categories. Two channels may not share a
  name (ignoring emojis and case).
- `[guild]` sets server defaults: `verification_level` (none / low / medium / high / very_high),
  `default_notifications` (all_messages / only_mentions), `explicit_content_filter`
  (disabled / members_without_roles / all_members). Remove a key to leave that setting alone.
- The bot finds its channels through `[channels]` in `config.toml` (`verify`, `giveaways`, `winners`,
  `tickets`, `ticket_log`, `self_roles`). Values are names (emojis ignored) or numeric ids. If you
  rename a channel in the template, update the matching value.
- Stage channels need a Community server, so the "stage" is a normal voice channel.

## How matching works

Existing roles, categories and channels are matched by name, ignoring case and anything before the
first letter or digit: `🎁┃giveaways`, `giveaways` and `Giveaways` are the same channel.

- Something that exists is never renamed, moved or deleted. A channel found in another category stays
  there (the plan says so).
- Permission overwrites are only **added** for roles or members that have none on that channel yet;
  an existing overwrite is never changed. To reset a channel to the template, remove its overwrites in
  Discord and build again.
- Server defaults are changed only where they differ.
- `build` saves the ids to `data/state.json` (roles by name, channels by core name). The bot prefers
  these ids, so renaming a role or channel in Discord afterwards does not break it.

## Running it

```bash
python -m kit.setup plan          # read-only: GET requests only; safe on any server
python -m kit.setup build         # asks for confirmation; --yes to skip the question
```

`build` creates roles first (permission overwrites need their ids), then each category followed by its
channels, then the server defaults. Run it again any time: with nothing missing it does nothing.
