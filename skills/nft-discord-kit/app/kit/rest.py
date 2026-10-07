"""A small Discord REST client with the bot token (setup commands, and adding members after OAuth)."""
from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import quote

import aiohttp

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (nft-discord-kit, 0.1.0)"
TRIES = 6
MAX_WAIT = 60.0  # never sleep longer than this on one rate limit
SERVER_ERROR_WAIT = 1.0  # a 5xx is retried twice, after 1 s and 2 s


class DiscordError(Exception):
    def __init__(self, status: int, code: int, message: str):
        super().__init__(f"HTTP {status}, Discord code {code}: {message}")
        self.status, self.code = status, code


def _retry_after(data: Any, headers: Any) -> float:
    """Seconds to wait on a 429: the JSON body's retry_after, else the Retry-After header, else 1."""
    for value in ((data or {}).get("retry_after") if isinstance(data, dict) else None, headers.get("Retry-After")):
        try:
            return min(MAX_WAIT, max(0.0, float(value)))
        except (TypeError, ValueError):
            continue
    return 1.0


class Rest:
    def __init__(self, session: aiohttp.ClientSession, token: str, read_only: bool = False):
        self.session, self.read_only = session, read_only
        self._headers = {"Authorization": f"Bot {token}", "User-Agent": USER_AGENT}

    async def request(self, method: str, path: str, body: Any = None, reason: str = "") -> Any:
        """Call the API. Waits out 429s and retries 5xx a few times; raises DiscordError otherwise, also when
        the answer is not JSON (a proxy's HTML error page). A read-only client refuses anything but GET
        before it reaches the network."""
        if self.read_only and method.upper() != "GET":
            raise PermissionError(f"read-only Discord client: refusing {method} {path}")
        headers = dict(self._headers)
        if reason:
            headers["X-Audit-Log-Reason"] = quote(reason)
        status, data, text = 0, None, ""
        for attempt in range(TRIES):
            async with self.session.request(method, API + path, json=body, headers=headers,
                                            timeout=aiohttp.ClientTimeout(total=30)) as resp:
                status, text = resp.status, await resp.text()
                try:
                    data = json.loads(text) if text else None
                except ValueError:
                    data = None
                if status == 429:
                    await asyncio.sleep(_retry_after(data, resp.headers) + 0.1)
                    continue
            if status >= 500 and attempt < 2:
                await asyncio.sleep(SERVER_ERROR_WAIT * (attempt + 1))
                continue
            if status == 204:
                return None
            if status >= 400:
                info = data if isinstance(data, dict) else {}
                raise DiscordError(status, int(info.get("code") or 0), str(info.get("message") or text[:120]))
            if data is None:
                raise DiscordError(status, 0, f"not a JSON answer: {text[:120]}")
            return data
        raise DiscordError(status, 0, "still failing after several tries")
