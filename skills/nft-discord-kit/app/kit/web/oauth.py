"""Discord OAuth2 (authorization code flow) for the optional website login."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import aiohttp

AUTHORIZE = "https://discord.com/oauth2/authorize"
TOKEN = "https://discord.com/api/v10/oauth2/token"
ME = "https://discord.com/api/v10/users/@me"


class OAuthError(Exception):
    pass


def authorize_url(client_id: int, redirect_uri: str, state: str, join: bool) -> str:
    scope = "identify guilds.join" if join else "identify"
    return AUTHORIZE + "?" + urlencode({"client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri,
                                        "scope": scope, "state": state, "prompt": "none"})


async def exchange_code(session: aiohttp.ClientSession, client_id: int, client_secret: str, code: str,
                        redirect_uri: str) -> dict[str, Any]:
    """Swap the code for a token answer: {"access_token", "scope", "expires_in", ...}."""
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri}
    async with session.post(TOKEN, data=form, auth=aiohttp.BasicAuth(str(client_id), client_secret),
                            timeout=aiohttp.ClientTimeout(total=20)) as resp:
        data = await resp.json(content_type=None)
    if resp.status != 200 or not isinstance(data, dict) or not data.get("access_token"):
        raise OAuthError(str((data or {}).get("error") if isinstance(data, dict) else resp.status))
    return data


async def fetch_user(session: aiohttp.ClientSession, access_token: str) -> dict[str, str]:
    """{"id", "name"} of the token's owner."""
    async with session.get(ME, headers={"Authorization": f"Bearer {access_token}"},
                           timeout=aiohttp.ClientTimeout(total=20)) as resp:
        data = await resp.json(content_type=None)
    if resp.status != 200 or not isinstance(data, dict) or not str(data.get("id", "")).isdigit():
        raise OAuthError(f"users/@me answered {resp.status}")
    return {"id": str(data["id"]), "name": str(data.get("global_name") or data.get("username") or "")[:64]}
