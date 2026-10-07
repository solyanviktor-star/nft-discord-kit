"""The verification website: the verify flow end to end, abuse limits, the Discord login, website raffles."""
from __future__ import annotations

import base64
import ipaddress
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from aiohttp.test_utils import make_mocked_request
from eth_account import Account
from eth_account.messages import encode_defunct
from nacl.signing import SigningKey

from conftest import b58encode, make_settings
from fakes import make_world
from kit import statetoken
from kit.web import app as web_app
from kit.web.app import Site


def evm_sign(account, message: str) -> str:
    return "0x" + bytes(account.sign_message(encode_defunct(text=message)).signature).hex()


async def start(aiohttp_client, tmp_path, reader, **over):
    w = make_world(tmp_path, make_settings(**over.pop("settings", {})), reader)
    site = Site(w.kit, http=None, application_id=lambda: 1234)  # type: ignore[arg-type]
    client = await aiohttp_client(site.app())
    return w, site, client


def state_for(w, member) -> str:
    return w.kit.verify_url(str(member.id), member.name).split("state=", 1)[1]


async def link_evm(client, state, account, sign=None):
    r = await client.post("/api/nonce", json={"state": state, "kind": "evm", "address": account.address})
    assert r.status == 200, await r.text()
    challenge = await r.json()
    signature = (sign or evm_sign)(account, challenge["message"])
    return challenge, await client.post("/api/link", json={"state": state, "nonce": challenge["nonce"],
                                                            "signature": signature})


async def test_verify_flow_nonce_sign_link_roles(aiohttp_client, tmp_path, reader):
    w, _, client = await start(aiohttp_client, tmp_path, reader)
    account = Account.create()
    reader.counts[account.address.lower()] = 3
    state = state_for(w, w.newbie)
    me = await (await client.get("/api/me", params={"state": state})).json()
    assert me["user"] == {"id": str(w.newbie.id), "name": "newbie"} and me["wallets"] == []
    challenge, r = await link_evm(client, state, account)
    message = challenge["message"]
    assert message.startswith(f"verify.example.com wants you to sign in with your Ethereum account:\n{account.address}\n")
    assert f"Nonce: {challenge['nonce']}" in message and "URI: https://verify.example.com/verify" in message
    assert "Discord account newbie" in message and "not a transaction" in message
    body = await r.json()
    assert r.status == 200, body
    assert body["summary"] == "3 NFTs found — roles Collector, Holder given"
    assert body["found"] == 3 and body["member"] and not body["moved"]
    assert {role.name for role in w.newbie.roles} == {"Holder", "Collector"}  # given by the bot, same process
    assert [x.address for x in w.kit.store.wallets_of(str(w.newbie.id))] == [account.address.lower()]
    again = await client.post("/api/link", json={"state": state, "nonce": challenge["nonce"],
                                                 "signature": evm_sign(account, message)})
    assert again.status == 400 and "already used" in (await again.json())["error"]  # single-use nonce


async def test_wrong_signer_links_nothing(aiohttp_client, tmp_path, reader):
    w, _, client = await start(aiohttp_client, tmp_path, reader)
    someone_else = Account.create()
    _, r = await link_evm(client, state_for(w, w.newbie), Account.create(),
                          sign=lambda acct, msg: evm_sign(someone_else, msg))
    assert r.status == 400
    assert (await r.json())["error"] == "The signature does not match this wallet. Nothing was linked."
    assert w.kit.store.wallets_of(str(w.newbie.id)) == []


async def test_contract_wallet_eip1271(aiohttp_client, tmp_path, reader):
    w, _, client = await start(aiohttp_client, tmp_path, reader)
    safe, owner = Account.create(), Account.create()
    reader.contract_wallets.add(safe.address.lower())  # the "contract" vouches for its owner's signature
    _, r = await link_evm(client, state_for(w, w.newbie), safe, sign=lambda acct, msg: evm_sign(owner, msg))
    assert r.status == 200 and w.kit.store.wallets_of(str(w.newbie.id))[0].address == safe.address.lower()


async def test_solana_wallet(aiohttp_client, tmp_path, reader):
    w, _, client = await start(aiohttp_client, tmp_path, reader)
    key = SigningKey.generate()
    address = b58encode(bytes(key.verify_key))
    reader.counts[address] = 1
    state = state_for(w, w.newbie)
    r = await client.post("/api/nonce", json={"state": state, "kind": "solana", "address": address})
    challenge = await r.json()
    assert challenge["message"].startswith(f"verify.example.com wants you to sign in with your Solana account:\n{address}")
    signature = base64.b64encode(key.sign(challenge["message"].encode()).signature).decode()
    r = await client.post("/api/link", json={"state": state, "nonce": challenge["nonce"], "signature": signature})
    assert r.status == 200 and (await r.json())["summary"] == "1 NFT found — roles Holder given"


async def test_wallet_moves_to_the_new_account(aiohttp_client, tmp_path, reader):
    w, _, client = await start(aiohttp_client, tmp_path, reader)
    account = Account.create()
    reader.counts[account.address.lower()] = 1
    w.kit.link_wallet(str(w.holder.id), "holder", "evm", account.address.lower())
    _, r = await link_evm(client, state_for(w, w.newbie), account)
    assert (await r.json())["moved"] is True
    assert w.kit.store.wallets_of(str(w.holder.id)) == []
    assert "Holder" not in {x.name for x in w.holder.roles}  # the old account lost its kit roles
    assert "Holder" in {x.name for x in w.newbie.roles}


async def test_bad_and_expired_links(aiohttp_client, tmp_path, reader):
    w, site, client = await start(aiohttp_client, tmp_path, reader)
    old = statetoken.sign(statetoken.VERIFY, {"u": "3002", "n": "newbie", "g": "1000"}, w.kit.secret, 60,
                          now=time.time() - 3600)
    r = await client.get("/api/me", params={"state": old})
    assert r.status == 400 and (await r.json())["error"] == "Link expired — press Verify in Discord again."
    good = state_for(w, w.newbie)
    r = await client.get("/api/me", params={"state": good[:-1] + ("0" if good[-1] != "0" else "1")})
    assert (await r.json())["error"].startswith("This link is not valid")
    r = await client.get("/api/me")
    assert r.status == 401
    other_guild = statetoken.sign(statetoken.VERIFY, {"u": "3002", "n": "x", "g": "999"}, w.kit.secret, 60)
    r = await client.get("/api/me", params={"state": other_guild})
    assert (await r.json())["error"] == "This link belongs to another server."
    r = await client.post("/api/nonce", json={"state": good, "kind": "evm", "address": "0x1234"})
    assert (await r.json())["error"] == "That wallet address does not look right."


LOOPBACK = str(ipaddress.IPv4Address(0x7F000001))  # a proxy on the same machine
PUBLIC = str(ipaddress.IPv4Address(0x0B000001))  # any address on the internet


async def test_forwarded_for_is_believed_only_from_a_proxy(tmp_path, reader):
    def request(peer, forwarded):
        transport = SimpleNamespace(get_extra_info=lambda name, default=None: (peer, 4321) if name == "peername"
                                    else default)
        return make_mocked_request("GET", "/api/me", headers={"X-Forwarded-For": forwarded}, transport=transport)

    w = make_world(tmp_path, make_settings(), reader)
    site = Site(w.kit, http=None, application_id=lambda: 1)  # type: ignore[arg-type]
    assert site.client_ip(request(PUBLIC, "made-up")) == PUBLIC  # a direct client cannot pick its own key
    assert site.client_ip(request(LOOPBACK, "made-up, real-client")) == "real-client"  # the hop the proxy added
    off = Site(make_world(tmp_path / "off", make_settings(web={"trust_proxy": False}), reader).kit, http=None,
               application_id=lambda: 1)  # type: ignore[arg-type]
    assert off.client_ip(request(LOOPBACK, "real-client")) == LOOPBACK


async def test_limits_and_headers(aiohttp_client, tmp_path, reader):
    w, _, client = await start(aiohttp_client, tmp_path, reader, settings={"web": {"rate_limit_per_minute": 3}})
    page = await client.get("/verify")
    assert page.status == 200 and "default-src 'none'" in page.headers["Content-Security-Policy"]
    assert "Test Project" in await page.text()
    assert "--accent: #5865f2" in await (await client.get("/theme.css")).text()
    assert (await (await client.get("/healthz")).json())["ok"] is True
    r = await client.post("/api/nonce", json={}, headers={"Origin": "https://evil.example"})
    assert r.status == 403
    r = await client.post("/api/nonce", data="state=x")
    assert r.status == 415  # JSON only: a plain cross-site form cannot post here
    statuses = [(await client.get("/api/me")).status for _ in range(3)]
    assert statuses[-1] == 429


@pytest.fixture
def fake_oauth(monkeypatch):
    seen = {}

    async def exchange(session, client_id, secret, code, redirect_uri):
        seen["exchange"] = (client_id, code, redirect_uri)
        return {"access_token": "user-token", "scope": "identify guilds.join"}

    async def user(session, token):
        return {"id": "3002", "name": "newbie"}

    monkeypatch.setattr(web_app.oauth, "exchange_code", exchange)
    monkeypatch.setattr(web_app.oauth, "fetch_user", user)
    return seen


WEB = {"project": {"public_url": "http://localhost:8080"}, "web_raffles": {"enabled": True}}


async def login(client, next_page="/raffles"):
    r = await client.get("/auth/discord", params={"next": next_page}, allow_redirects=False)
    assert r.status == 302
    state = parse_qs(urlsplit(r.headers["Location"]).query)["state"][0]
    return await client.get("/auth/discord/callback", params={"code": "c0de", "state": state}, allow_redirects=False)


async def test_discord_login_and_website_raffles(aiohttp_client, tmp_path, reader, fake_oauth):
    w = make_world(tmp_path, make_settings(env={"DISCORD_CLIENT_SECRET": "secret"}, **WEB), reader)
    client = await aiohttp_client(Site(w.kit, http=None, application_id=lambda: 1234).app())  # type: ignore[arg-type]
    r = w.kit.create_raffle(title="Web Drop", link="https://example.com", chain="Ethereum", duration=3600, gtd=1,
                            fcfs=0, created_by="1", channel_id=str(w.channels["giveaways"].id), eligible=[])
    nobody = await client.post(f"/api/raffles/{r.id}/enter", json={})
    assert nobody.status == 401
    start = await client.get("/auth/discord", params={"next": "/raffles"}, allow_redirects=False)
    assert start.headers["Location"].startswith("https://discord.com/oauth2/authorize?client_id=1234")
    done = await login(client)
    assert done.status == 302 and done.headers["Location"] == "/raffles"
    assert fake_oauth["exchange"] == (1234, "c0de", "http://localhost:8080/auth/discord/callback")
    listing = await (await client.get("/api/raffles")).json()
    assert listing["user"]["id"] == "3002" and listing["raffles"][0]["entered"] is False
    assert "wallet" not in str(listing)
    entered = await (await client.post(f"/api/raffles/{r.id}/enter", json={})).json()
    assert entered["code"] == "no_profile"  # same rules as the button: a linked wallet first
    account = Account.create()
    reader.counts[account.address.lower()] = 2
    w.kit.link_wallet("3002", "newbie", "evm", account.address.lower())
    entered = await (await client.post(f"/api/raffles/{r.id}/enter", json={})).json()
    assert entered["code"] == "entered" and entered["message"].startswith("You're in! **2 tickets**")
    listing = await client.get("/api/raffles")
    assert listing.headers["Access-Control-Allow-Origin"] == "*" and (await listing.json())["raffles"][0]["entered"]
    page = await client.get("/raffles")
    assert page.status == 200
    assert (await client.get("/embed.js")).status == 200


async def test_no_open_redirect_and_expired_login(aiohttp_client, tmp_path, reader, fake_oauth):
    w = make_world(tmp_path, make_settings(env={"DISCORD_CLIENT_SECRET": "secret"}, **WEB), reader)
    client = await aiohttp_client(Site(w.kit, http=None, application_id=lambda: 1234).app())  # type: ignore[arg-type]
    done = await login(client, next_page="https://evil.example/steal")
    assert done.headers["Location"] == "/verify"
    client.session.cookie_jar.clear()
    r = await client.get("/auth/discord/callback", params={"code": "x", "state": "y"}, allow_redirects=False)
    assert r.headers["Location"] == "/?e=expired"
    page = await client.get("/", params={"e": "expired"})
    assert "That login attempt expired" in await page.text()


async def test_auto_join_after_verifying(aiohttp_client, tmp_path, reader, fake_oauth):
    w = make_world(tmp_path, make_settings(env={"DISCORD_CLIENT_SECRET": "secret"},
                                           project={"public_url": "http://localhost:8080"},
                                           oauth={"auto_join": True}), reader)
    site = Site(w.kit, http=None, application_id=lambda: 1234)  # type: ignore[arg-type]
    client = await aiohttp_client(site.app())
    del w.guild.members[w.newbie.id]  # not in the server yet
    joined = {}

    async def add_member(user_id, token, roles):
        joined.update(user=user_id, token=token, roles=roles)
        w.guild.members[w.newbie.id] = w.newbie
        await w.newbie.add_roles(*[w.role(n) for n in roles])
        return True

    w.bot.port.add_member = add_member
    await login(client, "/verify")
    account = Account.create()
    reader.counts[account.address.lower()] = 1
    r = await client.post("/api/nonce", json={"kind": "evm", "address": account.address})  # the session, no state
    challenge = await r.json()
    r = await client.post("/api/link", json={"nonce": challenge["nonce"], "signature": evm_sign(account, challenge["message"])})
    body = await r.json()
    assert joined == {"user": "3002", "token": "user-token", "roles": {"Holder"}}
    assert body["member"] is True and "Holder" in body["roles"]
