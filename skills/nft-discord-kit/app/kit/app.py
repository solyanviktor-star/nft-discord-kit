"""`python -m kit run`: the Discord bot and the website in one asyncio loop, sharing one SQLite store."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from pathlib import Path

import aiohttp
from aiohttp import web

from .bot import KitBot
from .config import Settings, session_secret
from .reader import HoldingsReader
from .service import Kit
from .store import Store
from .web import Site

log = logging.getLogger("kit")


async def run(s: Settings, data_dir: Path) -> None:
    store = Store(data_dir / "kit.db")
    secret = session_secret(s.env, data_dir)
    async with aiohttp.ClientSession(headers={"User-Agent": "nft-discord-kit"}) as http:
        kit = Kit(s, store, HoldingsReader(s, http), secret)
        bot = KitBot(kit, http, data_dir)
        kit.discord = bot.port
        site = Site(kit, http, application_id=lambda: s.discord.application_id or bot.application_id,
                    ready=bot.is_ready)
        runner = web.AppRunner(site.app(), access_log=None)
        await runner.setup()
        await web.TCPSite(runner, s.web.host or None, s.web.port).start()
        log.info("website on port %d, public URL %s", s.web.port, s.project.public_url)
        stop = asyncio.Event()
        with contextlib.suppress(NotImplementedError):  # no signal handlers on Windows; Ctrl+C still works
            for sig in (signal.SIGTERM, signal.SIGINT):
                asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        bot_task = asyncio.create_task(bot.start(s.discord_token))
        stop_task = asyncio.create_task(stop.wait())
        try:
            done, _ = await asyncio.wait({bot_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
            if bot_task in done:
                bot_task.result()  # surfaces a bad token or a missing intent
        finally:
            stop_task.cancel()
            await bot.close()
            await runner.cleanup()
            store.close()
            log.info("stopped")
