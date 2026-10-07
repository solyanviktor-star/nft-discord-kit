"""The website: the verify page and its JSON API, the optional Discord login, the optional raffle pages.

Abuse limits: single-use nonces that expire in 10 minutes, a per-IP rate limit on /api and /auth,
signed expiring state tokens, a fixed list of redirect targets (no open redirects), SameSite=Lax
HttpOnly cookies, JSON-only POSTs with an Origin check, and a strict Content-Security-Policy.
"""
from __future__ import annotations

import asyncio
import hmac
import html
import logging
import re
import secrets
import time
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web

from .. import statetoken
from ..nonces import Challenge, NonceBook
from ..raffles import ENDED, OPEN
from ..service import Kit
from ..signatures import (decode_signature, evm_message, evm_recover, normalize_address, solana_message,
                          solana_verify)
from . import oauth

log = logging.getLogger("kit.web")
HERE = Path(__file__).parent
SESSION_COOKIE, OAUTH_COOKIE = "kit_session", "kit_oauth"
SESSION_TTL = 7 * 86400
JOIN_TOKEN_TTL = 30 * 60
NEXT_PAGES = ("/verify", "/raffles", "/")
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' https: data:; "
       "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
INDEX_NOTICES = {"expired": "That login attempt expired. Please try again.",
                 "denied": "Discord login was cancelled.",
                 "failed": "Discord login failed. Please try again in a minute."}


class Fail(Exception):
    """An error answered as JSON {"error": message}."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class RateLimiter:
    """A token bucket per key: `per_minute` requests, refilled continuously."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic):
        self.cap, self.rate, self.clock = float(per_minute), per_minute / 60.0, clock
        self.buckets: dict[str, tuple[float, float]] = {}

    def allow(self, key: str) -> bool:
        now = self.clock()
        tokens, last = self.buckets.get(key, (self.cap, now))
        tokens = min(self.cap, tokens + (now - last) * self.rate)
        if len(self.buckets) > 50_000:  # memory guard against address spraying
            self.buckets.clear()
        allowed = tokens >= 1
        self.buckets[key] = (tokens - 1 if allowed else tokens, now)
        return allowed


async def json_body(request: web.Request) -> dict[str, Any]:
    if request.content_type != "application/json":
        raise Fail(415, "Send JSON.")
    try:
        data = await request.json()
    except ValueError:
        raise Fail(400, "Bad JSON.") from None
    if not isinstance(data, dict):
        raise Fail(400, "Bad JSON.")
    return data


class Site:
    def __init__(self, kit: Kit, http: aiohttp.ClientSession, application_id: Callable[[], int | None],
                 ready: Callable[[], bool] = lambda: True):
        self.kit, self.s, self.http = kit, kit.s, http
        self.application_id, self.ready = application_id, ready
        self.nonces = NonceBook()
        self.limiter = RateLimiter(kit.s.web.rate_limit_per_minute)
        self.join_tokens: dict[str, tuple[str, float]] = {}  # Discord id -> (OAuth token, expiry), for auto-join
        self.secure = kit.s.project.public_url.startswith("https://")
        self.redirect_uri = kit.s.project.public_url + "/auth/discord/callback"
        self.pages = {name: self._render(name) for name in ("index.html", "verify.html", "raffles.html")}

    def app(self) -> web.Application:
        app = web.Application(middlewares=[self.guard], client_max_size=64 * 1024)
        add_get, add_post = app.router.add_get, app.router.add_post
        add_get("/healthz", self.healthz)
        add_get("/favicon.ico", self.no_icon)
        add_get("/", self.page_handler("index.html"))
        add_get("/verify", self.page_handler("verify.html"))
        add_get("/theme.css", self.theme)
        app.router.add_static("/static/", HERE / "static")
        add_get("/api/me", self.api_me)
        add_post("/api/nonce", self.api_nonce)
        add_post("/api/link", self.api_link)
        add_get("/auth/discord", self.auth_start)
        add_get("/auth/discord/callback", self.auth_callback)
        add_post("/auth/logout", self.auth_logout)
        if self.s.web_raffles:
            add_get("/raffles", self.page_handler("raffles.html"))
            add_get("/api/raffles", self.api_raffles)
            add_post("/api/raffles/{rid}/enter", self.api_enter)
            add_get("/embed.js", self.embed_js)
        return app

    # plumbing ----------------------------------------------------------------------------------------
    @web.middleware
    async def guard(self, request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]):
        api = request.path.startswith(("/api/", "/auth/"))
        if api and not self.limiter.allow(self.client_ip(request)):
            return web.json_response({"error": "Too many requests. Wait a minute and try again."}, status=429)
        origin = request.headers.get("Origin")
        if request.method == "POST" and origin and urlsplit(origin).netloc != request.host:
            return web.json_response({"error": "Cross-site request refused."}, status=403)
        try:
            resp = await handler(request)
        except Fail as e:
            resp = web.json_response({"error": e.message}, status=e.status)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if resp.content_type == "text/html":
            resp.headers["Content-Security-Policy"] = CSP
        if api:
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    def client_ip(self, request: web.Request) -> str:
        """Behind a reverse proxy the last X-Forwarded-For hop is the one the proxy itself added."""
        forwarded = request.headers.get("X-Forwarded-For", "") if self.s.web.trust_proxy else ""
        return forwarded.split(",")[-1].strip() if forwarded else (request.remote or "?")

    def _render(self, name: str) -> str:
        p = self.s.project
        logo = (f'<img class="logo" src="{html.escape(p.logo_url)}" alt="">' if p.logo_url
                else f'<span class="logo mark">{html.escape(p.name[:1].upper())}</span>')
        flags = (f'data-oauth="{int(self.s.oauth_enabled)}" data-raffles="{int(self.s.web_raffles)}" '
                 f'data-solana="{int(self.s.solana)}"')
        actions = ('<a class="btn primary" href="/auth/discord?next=/verify">Verify with Discord</a>'
                   if self.s.oauth_enabled else
                   "<p>Open the verification channel in the Discord server and press <b>Verify</b>.</p>")
        if self.s.web_raffles:
            actions += '<a class="btn" href="/raffles">Giveaways</a>'
        text = (HERE / "pages" / name).read_text(encoding="utf-8")
        for key, value in (("project", html.escape(p.name)), ("logo", logo), ("flags", flags), ("actions", actions)):
            text = text.replace("{{" + key + "}}", value)
        return text

    def page_handler(self, name: str) -> Callable[[web.Request], Awaitable[web.Response]]:
        async def handler(request: web.Request) -> web.Response:
            text = self.pages[name]
            if name == "index.html":  # the OAuth callback sends people back here with ?e=<reason>
                text = text.replace("{{notice}}", html.escape(INDEX_NOTICES.get(request.query.get("e", ""), "")))
            return web.Response(text=text, content_type="text/html")
        return handler

    async def no_icon(self, request: web.Request) -> web.Response:
        """Browsers ask for /favicon.ico on every page; answer without a 404 in the console."""
        return web.Response(status=204)

    async def healthz(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "discord": self.ready()})

    async def theme(self, request: web.Request) -> web.Response:
        c = self.s.project.color
        light = 0.299 * (c >> 16) + 0.587 * ((c >> 8) & 255) + 0.114 * (c & 255) > 150
        return web.Response(text=f":root {{ --accent: #{c:06x}; --accent-ink: {'#0b0b0f' if light else '#ffffff'}; }}\n",
                            content_type="text/css")

    # who is asking -----------------------------------------------------------------------------------
    def session(self, request: web.Request) -> tuple[str, str] | None:
        try:
            p = statetoken.verify(statetoken.SESSION, request.cookies.get(SESSION_COOKIE, ""), self.kit.secret)
        except statetoken.TokenError:
            return None
        return str(p["u"]), str(p.get("n", ""))

    def identify(self, request: web.Request, state: Any) -> tuple[str, str]:
        """(Discord id, name) from the personal verify link, else from the website session."""
        if state:
            try:
                p = statetoken.verify(statetoken.VERIFY, str(state), self.kit.secret)
            except statetoken.TokenError as e:
                raise Fail(400, "Link expired — press Verify in Discord again." if str(e) == "expired"
                           else "This link is not valid — press Verify in Discord again.") from None
            if p.get("g") and p["g"] != str(self.s.discord.guild_id):
                raise Fail(400, "This link belongs to another server.")
            return str(p["u"]), str(p.get("n", ""))
        who = self.session(request)
        if who:
            return who
        raise Fail(401, "Open this page from the Link Wallet button in Discord"
                   + (", or log in with Discord." if self.s.oauth_enabled else "."))

    def wallets(self, user_id: str) -> list[dict[str, Any]]:
        return [{"kind": w.kind, "address": w.address, "linked_at": w.linked_at}
                for w in self.kit.store.wallets_of(user_id)]

    # verification API --------------------------------------------------------------------------------
    async def api_me(self, request: web.Request) -> web.Response:
        uid, name = self.identify(request, request.query.get("state"))
        return web.json_response({"user": {"id": uid, "name": name}, "wallets": self.wallets(uid),
                                  "solana": self.s.solana, "project": self.s.project.name})

    async def api_nonce(self, request: web.Request) -> web.Response:
        body = await json_body(request)
        uid, name = self.identify(request, body.get("state"))
        kind = body.get("kind")
        if kind not in ("evm", "solana") or (kind == "solana" and not self.s.solana):
            raise Fail(400, "This wallet type is not enabled here.")
        address = normalize_address(kind, str(body.get("address", "")))
        if not address:
            raise Fail(400, "That wallet address does not look right.")
        url = self.s.project.public_url
        domain = urlsplit(url).netloc
        who = re.sub(r"\s+", " ", name).strip()[:64] or "this account"
        statement = (f"Link this wallet to the Discord account {who} (id {uid}) for {self.s.project.name}. "
                     "This is a free signature, not a transaction.")

        def make(nonce: str, issued: float, expires: float) -> str:
            if kind == "evm":
                return evm_message(domain, url + "/verify", address, statement, self.s.chain_id, nonce, issued, expires)
            return solana_message(domain, address, statement, nonce, issued, expires)

        challenge = self.nonces.issue(uid, name, kind, address, make)
        return web.json_response({"nonce": challenge.nonce, "message": challenge.message})

    async def signature_ok(self, challenge: Challenge, signature: bytes) -> bool:
        if challenge.kind == "solana":
            return solana_verify(challenge.address, challenge.message, signature)
        if evm_recover(challenge.message, "0x" + signature.hex()) == challenge.address:
            return True
        return await self.kit.reader.contract_signature_ok(challenge.address, challenge.message, signature)

    async def api_link(self, request: web.Request) -> web.Response:
        body = await json_body(request)
        uid, name = self.identify(request, body.get("state"))
        challenge = self.nonces.take(str(body.get("nonce", "")))
        if challenge is None or challenge.user_id != uid:
            raise Fail(400, "This sign-in request expired or was already used. Press Connect again.")
        signature = decode_signature(challenge.kind, str(body.get("signature", "")))
        if signature is None or not await self.signature_ok(challenge, signature):
            raise Fail(400, "The signature does not match this wallet. Nothing was linked.")
        previous = self.kit.link_wallet(uid, name or challenge.user_name, challenge.kind, challenge.address)
        report = await self.kit.sync_user(uid, "wallet linked on the website")
        token = self.take_join_token(uid) if not report.member and self.s.auto_join else None
        if token and await self.kit.auto_join(uid, token):
            report = await self.kit.sync_user(uid, "joined the server after verifying")
        if previous:
            await self.kit.sync_user(previous, "wallet moved to another Discord account")
        log.info("wallet linked: %s ...%s for %s%s", challenge.kind, challenge.address[-6:], uid,
                 " (moved from another account)" if previous else "")
        return web.json_response({
            "ok": True, "moved": bool(previous), "summary": report.summary(), "member": report.member,
            "found": report.holdings.total if report.holdings else None, "roles_added": report.added,
            "roles": report.roles, "wallets": self.wallets(uid)})

    # Discord login (OAuth2 code flow) ------------------------------------------------------------------
    def take_join_token(self, user_id: str) -> str | None:
        token, expires = self.join_tokens.pop(user_id, ("", 0.0))
        return token if token and expires > time.time() else None

    async def auth_start(self, request: web.Request) -> web.Response:
        if not self.s.oauth_enabled:
            raise web.HTTPNotFound()
        app_id = self.application_id()
        if not app_id:
            raise Fail(503, "The bot is still starting. Try again in a moment.")
        nxt = request.query.get("next", "/verify")
        state = secrets.token_urlsafe(24)
        resp = web.HTTPFound(oauth.authorize_url(app_id, self.redirect_uri, state, self.s.auto_join))
        resp.set_cookie(OAUTH_COOKIE, statetoken.sign(statetoken.OAUTH, {"s": state, "next": nxt if nxt in NEXT_PAGES
                                                                          else "/verify"}, self.kit.secret, 600),
                        max_age=600, path="/auth", httponly=True, samesite="Lax", secure=self.secure)
        raise resp

    async def auth_callback(self, request: web.Request) -> web.Response:
        if not self.s.oauth_enabled:
            raise web.HTTPNotFound()
        try:
            saved = statetoken.verify(statetoken.OAUTH, request.cookies.get(OAUTH_COOKIE, ""), self.kit.secret)
        except statetoken.TokenError:
            raise web.HTTPFound("/?e=expired") from None
        if request.query.get("error"):
            raise web.HTTPFound("/?e=denied")
        if not hmac.compare_digest(str(saved.get("s", "")), request.query.get("state", "")):
            raise web.HTTPFound("/?e=expired")
        try:
            token = await oauth.exchange_code(self.http, self.application_id() or 0, self.s.client_secret,
                                              request.query.get("code", ""), self.redirect_uri)
            user = await oauth.fetch_user(self.http, token["access_token"])
        except (oauth.OAuthError, aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("discord login failed: %s", e)
            raise web.HTTPFound("/?e=failed") from None
        if "guilds.join" in str(token.get("scope", "")).split():
            now = time.time()
            self.join_tokens = {k: v for k, v in self.join_tokens.items() if v[1] > now}
            self.join_tokens[user["id"]] = (token["access_token"], now + JOIN_TOKEN_TTL)
        resp = web.HTTPFound(saved["next"] if saved.get("next") in NEXT_PAGES else "/verify")
        resp.set_cookie(SESSION_COOKIE, statetoken.sign(statetoken.SESSION, {"u": user["id"], "n": user["name"]},
                                                        self.kit.secret, SESSION_TTL),
                        max_age=SESSION_TTL, path="/", httponly=True, samesite="Lax", secure=self.secure)
        resp.del_cookie(OAUTH_COOKIE, path="/auth")
        raise resp

    async def auth_logout(self, request: web.Request) -> web.Response:
        resp = web.json_response({"ok": True})
        resp.del_cookie(SESSION_COOKIE, path="/")
        return resp

    # optional: raffles on the website ------------------------------------------------------------------
    async def api_raffles(self, request: web.Request) -> web.Response:
        """Open and recently ended giveaways; never wallets. Readable from any site (embed.js)."""
        who = self.session(request)
        mine = self.kit.store.entered_raffles(who[0]) if who else set()
        counts = self.kit.store.entry_counts()
        since = self.kit.now() - self.s.web_raffles_recent_days * 86400
        items = []
        for r in sorted(self.kit.store.raffles(), key=lambda x: x.ends):
            if r.status == OPEN or (r.status == ENDED and int(r.result.get("at") or 0) >= since):
                items.append({"id": r.id, "title": r.title, "link": r.link, "chain": r.chain, "ends": r.ends,
                              "status": r.status, "gtd": r.gtd, "fcfs": r.fcfs, "winners": r.winners,
                              "entrants": counts.get(r.id, 0), "image": r.image, "description": r.description,
                              "entered": r.id in mine if who else None})
        resp = web.json_response({"project": self.s.project.name, "raffles": items,
                                  "user": {"id": who[0], "name": who[1]} if who else None})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    async def api_enter(self, request: web.Request) -> web.Response:
        await json_body(request)
        who = self.session(request)
        if not who:
            raise Fail(401, "Log in with Discord first.")
        rid = request.match_info["rid"]
        member = await self.kit.discord.get_member(who[0]) if self.kit.discord else None
        result = await self.kit.enter(rid, member)
        return web.json_response({"code": result.code, "tickets": result.tickets,
                                  "message": self.kit.entry_text(self.kit.store.raffle(rid), result)})

    async def embed_js(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(HERE / "static" / "embed.js", headers={"Content-Type": "text/javascript"})
