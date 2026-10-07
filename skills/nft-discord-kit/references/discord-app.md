# Discord application and bot

Everything here happens at https://discord.com/developers/applications, signed in as the person who
will own the bot. The agent cannot click for them; read the steps out one at a time.

## 1. Create the application

1. **New Application**, type a name (the bot shows up under this name), accept the terms, **Create**.
2. Optional: **General Information**: an app icon and description.

## 2. The bot user and its token

1. **Bot** tab.
2. **Reset Token**, confirm, **Copy**. Paste it into `.env` as `DISCORD_TOKEN=...`
   (the person pastes it themselves; nobody else needs to see it). Discord shows a token once; if it
   is lost or leaked, reset it again and update `.env`.
3. **Public Bot**: off. Otherwise anyone with the client id could add the bot to their own server.
4. **Privileged Gateway Intents**: turn on **SERVER MEMBERS INTENT** and **Save Changes**.
   The bot needs it to see members join, read their roles and ping staff one by one.
   It does not need Presence or Message Content.

Without the members intent `python -m kit run` stops with "Turn on SERVER MEMBERS INTENT ...".

## 3. Optional: the website login (OAuth2)

Needed only for "Verify with Discord" on the site's start page, for adding verified people to the
server automatically (`oauth.auto_join`), and for raffles on the website (`references/website-raffles.md`).
Holders can always verify through the Discord panel without it.

1. **OAuth2** tab > **Redirects** > **Add Redirect**: `https://<verification domain>/auth/discord/callback`
   (exactly; for a local test `http://localhost:8080/auth/discord/callback`). **Save Changes**.
2. **Client Secret** > **Reset Secret**, copy it into `.env` as `DISCORD_CLIENT_SECRET=...`.
3. The client id is read from the bot token automatically; `discord.application_id` in `config.toml`
   is only needed if that lookup is blocked.

Scopes asked for: `identify` (who is logging in), plus `guilds.join` when `oauth.auto_join = true`
(lets the bot add the person to the server once their wallet is verified). The bot never stores
OAuth tokens on disk; a `guilds.join` token is kept in memory for 30 minutes and used once.

## 4. Invite the bot

`python -m kit.setup invite-url` prints the invite link (it reads the token from `.env`):

- `--admin` asks for Administrator. Simplest for the first `kit.setup build`, which creates roles
  (including one with Administrator for the Team) and channels.
- Without `--admin` it asks only for what the running bot needs: View Channels, Send Messages,
  Send Messages in Threads, Create Private Threads, Manage Threads, Embed Links, Attach Files,
  Read Message History, Mention Everyone (for the giveaway alert tag), Manage Roles, plus Create
  Invite when `oauth.auto_join` is on.

Scopes: `bot applications.commands`. The person opens the link, picks the server and authorizes.
After the build, they may untick Administrator on the bot's own role (Server Settings > Roles);
the build already gave the bot what it needs in each channel.

## 5. Role order matters

The bot can only give or take roles that sit **below its own role**. Roles created by
`kit.setup build` are created below the bot automatically. If the server already had roles called
Holder, Whale, Giveaway Alerts and so on, `kit.setup plan` warns about any that sit above the bot:
drag the bot's role above them in Server Settings > Roles.

## 6. Slash commands

The bot registers `/nft ...` for the one server in `discord.guild_id` when it starts; they appear at
once (no hour-long global propagation). If they do not show up: the bot was invited without the
`applications.commands` scope (use the invite link again) or the process is not running.
