# Discord application and bot

Only three steps need the person's own Discord account: creating the application (and copying its
token), creating the server, and approving the bot's invite. The person does them, or an agent with a
browser tool does them in browser mode after the person opts in (`references/browser-setup.md`).
Everything else the kit does with the bot token through Discord's official API.

The kit never logs into the person's Discord account and never drives their browser by itself; the
three account steps stay theirs, because Discord's rules forbid automating user accounts.

## 1. Create the application and save its token

At https://discord.com/developers/applications, signed in as the person who will own the bot:

1. **New Application**, type a name (the bot shows up under this name), accept the terms, **Create**.
2. **Bot** tab: turn **Public Bot** off (otherwise anyone with the client id could add the bot to
   their own server).
3. **Reset Token**, confirm (with 2FA if asked), **Copy**.
4. The agent runs `python -m kit.setup token --from-clipboard`: it reads the clipboard, checks the
   token with Discord (`GET /applications/@me`), writes the `DISCORD_TOKEN=` line of `.env` (creating
   or replacing only that line) and prints only the application's name. The token never passes through
   a chat or the agent's context. A token is shown once; if it is lost or leaked, reset it and run the
   command again. Without a desktop (a server over SSH) the clipboard cannot be read; then the person
   pastes it into `.env` by hand.

## 2. Intents, description, icon: `python -m kit.setup app`

The agent runs `python -m kit.setup app` (`invite-url` also runs it). With the bot token it:

- turns on the two privileged intents the bot needs, the same switches as the Bot tab's
  **Privileged Gateway Intents** (Discord sets the "limited" flags for a bot in fewer than 100
  servers; every other application flag stays as it is):
  - **Server Members**: the bot sees members join, reads their roles and pings staff one by one.
    Without it `python -m kit run` stops and says so.
  - **Message Content**: when a ticket is closed the bot reads the thread for the transcript; without
    it Discord hands out other people's messages with empty text and the transcript is blank (the log
    then says so). The bot reads message text for nothing else. Presence is not needed.
- sets the description ("<Project> holder verification, raffles and support") and the icon (downloaded
  from `project.logo_url`: PNG, JPEG or GIF up to 4 MB) when they are empty;
  `--force-branding` replaces existing ones.

If Discord refuses the intents (a verified app needs Discord's approval for them), the command prints
the manual step: Developer Portal > the application > **Bot** > **Privileged Gateway Intents** > turn on
**SERVER MEMBERS INTENT** and **MESSAGE CONTENT INTENT** > **Save Changes**. `python -m kit check` shows
the intents' state (on, limited, off) at any time.

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

## 4. The server and the invite

1. The person creates the server in the Discord app: **+** (Add a Server) > **Create My Own** >
   **For me and my friends** > a name > **Create**.
2. The agent runs `python -m kit.setup invite-url --admin`. It prints the invite link and, under it,
   the permissions the link asks for (scopes: `bot applications.commands`), then runs `app`.
3. The person opens the link, picks the server, presses **Continue** and **Authorize**.
4. Meanwhile the agent runs `python -m kit.setup guilds --wait`. It asks Discord every 5 seconds
   (up to 10 minutes, `--timeout`) which servers the bot is in; as soon as it is in exactly one, it
   writes that id into `config.toml` as `discord.guild_id` (one line edited in place, comments kept)
   and prints the server's name. If the bot is in several servers it lists them; ask the person which
   one, then `python -m kit.setup guilds --pick <id>`.

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
