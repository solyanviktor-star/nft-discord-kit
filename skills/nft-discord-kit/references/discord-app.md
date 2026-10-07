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
4. **Privileged Gateway Intents**: turn on **SERVER MEMBERS INTENT** and **MESSAGE CONTENT INTENT**,
   then **Save Changes**.
   - Server Members: the bot sees members join, reads their roles and pings staff one by one.
     Without it `python -m kit run` stops with "Turn on SERVER MEMBERS INTENT ...".
   - Message Content: when a ticket is closed the bot reads the thread for the transcript; without this
     switch Discord hands out other people's messages with empty text, and the transcript is blank
     (the log then says so). The bot reads message text for nothing else.
   - Presence is not needed.

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

`python -m kit.setup invite-url` prints the invite link (it reads the token from `.env`) and, under
it, the permissions the link asks for. Scopes: `bot applications.commands`. The person opens the
link, picks the server and authorizes.

Two sets of permissions matter:

- **For the build** (`kit.setup build`): Manage Roles, Manage Channels and Manage Server, plus every
  permission the template's roles and overwrites hand out, because Discord lets a bot grant or deny
  only permissions it has itself. The default template creates the Team role with Administrator, so
  the build needs Administrator: use `invite-url --admin`. `kit.setup plan` lists anything missing,
  and `build` refuses up front (changing nothing) instead of failing halfway.
- **For running** (what `invite-url` without `--admin` asks for): View Channels, Send Messages,
  Embed Links, Attach Files, Read Message History, Mention @everyone, @here and All Roles (the
  giveaway alert tag and staff pings), Manage Roles (holder roles, self-roles; this one can only come
  from the bot's own role, never from channel permissions), Manage Threads, Create Private Threads and
  Send Messages in Threads (tickets), plus Create Invite when `oauth.auto_join` is on (adding verified
  people to the server).

After the build you may drop Administrator, but not by simply unticking it: in Server Settings >
Roles > the bot's own role, first turn on every permission of the running set above, then turn off
Administrator, and keep the bot's role above the holder, tier, special and self-roles.
`python -m kit.setup plan` notes any permission the bot's role still lacks. (Kicking the bot and
inviting it again with the non-admin link also works, but its new role may land below the holder
roles; drag it back up.)

## 5. Role order matters

The bot can only give or take roles that sit **below its own role**. Roles created by
`kit.setup build` are created below the bot automatically. If the server already had roles called
Holder, Whale, Giveaway Alerts and so on, `kit.setup plan` warns about any that sit above the bot:
drag the bot's role above them in Server Settings > Roles.

## 6. Slash commands

The bot registers `/nft ...` for the one server in `discord.guild_id` when it starts; they appear at
once (no hour-long global propagation). If they do not show up: the bot was invited without the
`applications.commands` scope, or is not in that server (the log says "could not register the /nft
commands ..."; the bot keeps running, so open the invite link again and restart), or the process is
not running.
