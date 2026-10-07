# Holder verification

## The flow

1. In the verification channel the member presses **Verify**. First time (no wallet linked yet) the bot
   answers privately with a **Link Wallet** button: a personal link
   `PUBLIC_URL/verify?state=<base64 JSON>.<HMAC>` holding their Discord id, name, the server id and an
   expiry 30 minutes ahead, signed with HMAC-SHA256 and the session secret.
2. The page (plain HTML/CSS/JS, no build step, no third-party scripts) shows "Discord: name" and the
   wallets found in the browser: EVM wallets through EIP-6963 discovery with a `window.ethereum`
   fallback; with `verification.solana = true` also Phantom, Solflare and Backpack.
3. Connecting asks the wallet quietly for an already shared account first (`eth_accounts`), then for
   permission (`eth_requestAccounts`). Signing is a separate tap, because phone wallets only open the
   signing sheet right after a tap.
4. The page asks the server for a message (`POST /api/nonce`); the server checks the link, makes a
   single-use nonce that lives 10 minutes and returns the text to sign:
   - EVM: an EIP-4361 "Sign-In with Ethereum" message for the site's domain, with the checksummed
     address, a statement naming the Discord account, the nonce, issue and expiry times;
   - Solana: the same idea in plain text, signed with `signMessage`.
5. The wallet signs (free, not a transaction). `POST /api/link` sends the nonce and the signature.
   The server takes the nonce (it cannot be used again), then checks the signature:
   - EVM: recovers the signer with eth-account. If it is not the address and the address holds
     contract code on one of the configured chains, it asks the contract (EIP-1271
     `isValidSignature`), so Safe and other contract wallets work;
   - Solana: ed25519 with PyNaCl.
6. The wallet is linked to the Discord account; the server reads the holdings at once and the bot,
   in the same process, gives the roles. The page shows the result, e.g.
   "3 NFTs found - roles Collector, Holder given". No second click.

Afterwards **Verify** (or `/nft verify`) re-reads the chain on demand and fixes the roles.
**My Wallets** shows linked wallets, the raffle wallets, holdings and tickets; it can link another
wallet, set a raffle wallet, unlink a wallet, or refresh.

## Rules

- A wallet belongs to one Discord account at a time. A valid signature from another account moves it
  there; the page says so, and the previous account's roles are recalculated at once.
- An account may link several wallets; holdings add up across them.
- Unlinking (My Wallets > Unlink a wallet) recalculates the roles right away; with no wallets left the
  kit roles go.

## Roles and syncing

- Roles the kit manages: the tiers, collection roles and special roles from `config.toml`. Every other
  role of a member is never touched.
- Holdings are read with `balanceOf` (ERC-721), `balanceOf(address, id)` (ERC-1155) and `ownerOf`
  (special ERC-721 tokens), batched through Multicall3 `aggregate3` with `allowFailure`, with a plain
  `eth_call` fallback, several RPC URLs per chain and retries. Solana collections use DAS
  `getAssetsByOwner` (verified collection grouping only, burnt assets skipped).
- **Unknown is not zero.** If the node does not answer for someone's wallets, their tier and collection
  roles are left alone this round; if the special tokens could not be read, the special roles are left
  alone. The last good reading is stored in the database and used for tickets meanwhile.
- When roles are synced: right after linking (the page and, as a fallback, the bot's 30-second tick),
  when a linked member joins the server, on **Verify** and `/nft verify`, and for everyone every
  `verification.sync_minutes` (one batched read for all linked members).

## Website login (optional)

With `DISCORD_CLIENT_SECRET` set, the start page `/` offers **Verify with Discord**: the OAuth2 code
flow (scope `identify`, plus `guilds.join` with `oauth.auto_join`), then the same verify page, with
the person identified by a signed session cookie instead of the link. With auto-join, once the wallet
is verified the bot adds the person to the server with their roles.

## Security

- The verify link is HMAC-signed, names one Discord account and one server, and expires in 30 minutes.
  A link from another deployment (different server id) is refused.
- Nonces are single use and expire after 10 minutes; a signature is only accepted for the exact
  message the server issued for that account and address.
- Per-IP rate limit on `/api/*` and `/auth/*` (`web.rate_limit_per_minute`, default 30). The client
  address comes from `X-Forwarded-For` only when the request itself arrives from a loopback or
  private address (Caddy, Docker, a platform's router) and `web.trust_proxy` is on; a client talking
  to the app directly cannot choose its own rate-limit key.
- POST endpoints accept JSON only and refuse a foreign `Origin`, so plain cross-site forms cannot post.
- Cookies are HttpOnly and `SameSite=Lax` (and `Secure` on https). The OAuth `state` is random,
  kept in a signed cookie for 10 minutes and compared in constant time. After login the redirect goes
  only to `/verify`, `/raffles` or `/`: no open redirects.
- Every page sends a strict Content-Security-Policy (scripts, styles and requests only from the site
  itself; images also from https), `X-Content-Type-Options: nosniff` and a same-origin referrer policy.
- Secrets never reach logs: RPC URLs are logged by host name only; tokens are never printed.

## Phones

A phone browser has no wallet extensions, so the page offers links that reopen it inside a wallet's
own browser (MetaMask, Coinbase Wallet, Trust Wallet, Phantom, and Solflare when Solana is on) and a
**Copy link** button. Inside Discord's in-app browser it first asks to open the page in a real
browser. Inside a wallet's browser it shows no such links and waits a few seconds for the wallet to
appear, since some wallets inject themselves late.

Those "Open in ..." links hand the page's full address, including the personal verify token, to
the wallet maker's link service (e.g. `metamask.app.link`), because the wallet has to reopen exactly
this page. The impact is low: the token expires 30 minutes after Verify was pressed, it cannot move
or unlink anything, and linking a wallet still needs that wallet's signature. Within those minutes
someone holding the token could see which wallets that Discord account has linked, or link a wallet
of their own to it. The links appear only on a phone browser that has no wallet; the **Copy link**
button and a desktop extension avoid them.
