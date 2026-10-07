"""A small Discord REST client with the bot token (setup commands, and adding members after OAuth)."""
from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

import aiohttp

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (nft-discord-kit, 0.1.0)"


class DiscordError(Exception):
    def __init__(self, status: int, code: int, message: str):
        super().__init__(f"HTTP {status}, Discord code {code}: {message}")
        self.status, self.code = status, code


class Rest:
    def __init__(self, session: aiohttp.ClientSession, token: str, read_only: bool = False):
        self.session, self.read_only = session, read_only
        self._headers = {"Authorization": f"Bot {token}", "User-Agent": USER_AGENT}

    async def request(self, method: str, path: str, body: Any = None, reason: str = "") -> Any:
        """Call the API; waits out 429 rate limits; raises DiscordError on other errors.

        A read-only client refuses anything but GET before it reaches the network.
        """
        if self.read_only and method.upper() != "GET":
            raise PermissionError(f"read-only Discord client: refusing {method} {path}")
        headers = dict(self._headers)
        if reason:
            headers["X-Audit-Log-Reason"] = quote(reason)
        for _ in range(6):
            async with self.session.request(method, API + path, json=body, headers=headers,
                                            timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 204:
                    return None
                data = await resp.json(content_type=None)
                if resp.status == 429:
                    await asyncio.sleep(float((data or {}).get("retry_after", 1)) + 0.1)
                    continue
                if resp.status >= 400:
                    data = data if isinstance(data, dict) else {}
                    raise DiscordError(resp.status, int(data.get("code") or 0), str(data.get("message") or ""))
                return data
        raise DiscordError(429, 0, "still rate limited after several waits")
