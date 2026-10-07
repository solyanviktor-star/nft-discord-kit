# Browser mode (optional)

For agents that have a browser tool (a Playwright MCP server or skill, Claude in Chrome, computer use).
It covers only the three setup steps that need the person's own Discord account: creating the
application (and copying its token), creating the server, approving the bot's invite. Everything else
stays with the kit's commands and Discord's API.

## When

Only after the person chooses it. Offer it in step 1 with one honest sentence, for example:

> I can click through the three Discord steps in a browser for you. Discord's rules forbid automating
> user accounts, so this is at your own risk; I would touch your account only for these three steps.
> Doing it yourself takes about three minutes, and I will tell you where to click.

If they prefer the manual way, follow `references/discord-app.md` and never open the browser.

## Logging in

- Open a **visible** browser window on the person's own computer, the same machine the kit runs on
  (the token travels through that machine's clipboard).
- The person logs in themselves. Never ask for, type or read a password, a 2FA code or an email code;
  wait until they say they are in.
- Captcha, 2FA, email confirmation, "is this you?" checks: stop, hand over to the person, continue
  only when they say so.

## The click path

Find elements by their visible labels, not by brittle selectors; if a label differs (Discord changes
its pages), describe what you see and let the person point.

1. **Application.** https://discord.com/developers/applications > **New Application** > the bot's
   name > tick the terms > **Create**.
2. **Bot** tab > turn **Public Bot** off (save if a bar asks to).
3. **Reset Token** > confirm (the person handles 2FA) > **Copy**. Then run
   `python -m kit.setup token --from-clipboard`: it checks the token with Discord, writes it into
   `.env` and prints only the application's name. If it says the clipboard holds no token (a
   headless or remote browser does not reach the system clipboard), ask the person to press **Copy**
   themselves and run the command again.
4. **Intents, description, icon:** run `python -m kit.setup app`. No clicking: it uses the API.
5. **Server.** In the Discord web app (https://discord.com/app): **+** (Add a Server) > **Create My Own**
   > **For me and my friends** > the server's name > **Create**.
6. **Invite.** Run `python -m kit.setup invite-url --admin` and open the printed link > pick the new
   server > **Continue** > **Authorize** (the person solves a captcha if one appears). Run
   `python -m kit.setup guilds --wait`: it sees the bot join and saves the server id into `config.toml`.

## Rules

- Never use the browser for anything the API or a kit command can do (intents, description, icon,
  server id, roles, channels, panels, messages).
- Never read the token off the page into your context: no screenshots or page text of the token
  field, no copying it into the chat. Always `python -m kit.setup token --from-clipboard`.
- Act only in this application and this server: no direct messages, no other servers, no messages,
  no settings beyond the steps above.
- Close the browser when the three steps are done.
- Verify each step through the API afterwards, not by looking at the page: the token command checks
  the token, `python -m kit check` shows the intents (on or limited), and `guilds --wait` confirms the
  bot is in the server.
