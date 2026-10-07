# Optional last stage: raffles on the project's website

Only set this up after asking the person "Do you also want holders to enter raffles from your
website?" and getting a yes. Everything else works without it.

## What it adds

- `GET /raffles`: a page on the verification site listing open raffles (and those ended in the last
  `recent_days`), with **Log in with Discord** and an **Enter** button per open raffle.
- `GET /api/raffles`: the same list as JSON for any site: id, title, link, chain, deadline, status,
  GTD/FCFS/winners counts, entrants, image, description. Never wallets or winners' ids. It is readable
  cross-origin (`Access-Control-Allow-Origin: *`, no cookies); on the verification site itself it also
  says which raffles the logged-in person entered.
- `POST /api/raffles/{id}/enter`: enters the logged-in person through **the same function as the
  Discord button**, with the same rules and replies: they must be in the server, have an eligible role,
  have a linked wallet, hold NFTs (unless `require_holding = false`), and have a wallet of the raffle's
  kind. Tickets are read from the chain right then, and the raffle card in Discord is redrawn on the
  usual schedule.
- `GET /embed.js`: a small script that lists the open raffles on any page and links to `/raffles`.

## Prerequisites

1. The website login: `DISCORD_CLIENT_SECRET` in `.env` and the redirect
   `https://<verification domain>/auth/discord/callback` in the Developer Portal (OAuth2 tab), as in
   `references/discord-app.md` step 3.
2. People still link wallets on the verify page or through **Verify** in Discord; the website login
   alone does not prove wallet ownership.

## Turn it on

```toml
[web_raffles]
enabled = true
recent_days = 7
```

Run `python -m kit check` (it refuses `enabled = true` without `DISCORD_CLIENT_SECRET`), restart
the process, open `https://<domain>/raffles`, log in, enter a test raffle.

## Show the list on the project's site

```html
<div id="nft-raffles"></div>
<script src="https://verify.example.com/embed.js" async></script>
```

Another element: `<script src=".../embed.js" data-target="#my-element" async></script>`. The script
builds plain list items (`ul.nft-raffles`) with no styles of its own, so the site's CSS applies.
Entering always happens on the verification site, where the login cookie lives.

## Security notes

- The login is a signed, HttpOnly, `SameSite=Lax` cookie valid for 7 days; logging out clears it.
- Entering requires a JSON POST from the verification site's own origin; other sites can read the
  public list but cannot enter on someone's behalf.
- No wallet addresses leave the server through these endpoints.
