"""The Discord application's own settings, set with the bot token through Discord's API:
the privileged intents the bot needs, and a description and icon for the bot's profile.

Only the "limited" intent flags can be set over the API (what the Bot tab's switches set for a bot in
fewer than 100 servers); a verified app needs Discord's approval, so then the manual step is printed.
"""
from __future__ import annotations

import base64
from typing import Any

import aiohttp

from .config import Settings
from .rest import DiscordError, Rest

PRESENCE_LIMITED = 1 << 13
MEMBERS, MEMBERS_LIMITED = 1 << 14, 1 << 15
CONTENT, CONTENT_LIMITED = 1 << 18, 1 << 19
EDITABLE_FLAGS = PRESENCE_LIMITED | MEMBERS_LIMITED | CONTENT_LIMITED  # all the API lets an app change
INTENTS = (("Server Members", MEMBERS, MEMBERS_LIMITED, "SERVER MEMBERS INTENT"),
           ("Message Content", CONTENT, CONTENT_LIMITED, "MESSAGE CONTENT INTENT"))
MAX_ICON_BYTES = 4 * 1024 * 1024
_FORMATS = ((b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpeg"), (b"GIF87a", "gif"), (b"GIF89a", "gif"))


def merge_intent_flags(flags: int) -> tuple[int, list[str]]:
    """(flags to send, intents being turned on). An intent counts as on when its full or its limited flag
    is set. The result keeps the editable bits that are set now, adds the limited flag of every intent
    that is off, and leaves out every other bit: the API accepts only the limited intent flags."""
    added = [name for name, full, limited, _ in INTENTS if not flags & (full | limited)]
    new = flags | sum(limited for name, _, limited, _ in INTENTS if name in added)
    return new & EDITABLE_FLAGS, added


def intent_states(flags: int) -> dict[str, str]:
    """"on" (approved full intent), "limited" (on, for a bot in fewer than 100 servers) or "off"."""
    return {name: "on" if flags & full else "limited" if flags & limited else "off"
            for name, full, limited, _ in INTENTS}


def icon_data_uri(raw: bytes) -> str:
    """A Discord image data URI for an icon; ValueError says why it cannot be one."""
    if len(raw) > MAX_ICON_BYTES:
        raise ValueError(f"the logo is larger than {MAX_ICON_BYTES // (1024 * 1024)} MB")
    kind = next((name for magic, name in _FORMATS if raw.startswith(magic)), None)
    if kind is None:
        raise ValueError("the logo is not a PNG, JPEG or GIF image")
    return f"data:image/{kind};base64,{base64.b64encode(raw).decode('ascii')}"


async def fetch_logo(session: aiohttp.ClientSession, url: str) -> bytes:
    """Download the logo, stopping as soon as it is larger than an icon may be."""
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
        if resp.status != 200:
            raise ValueError(f"the logo URL answered HTTP {resp.status}")
        data = b""
        async for chunk in resp.content.iter_chunked(64 * 1024):
            data += chunk
            if len(data) > MAX_ICON_BYTES:
                break
    return data


def default_description(s: Settings) -> str:
    return f"{s.project.name} holder verification, raffles and support"[:400]


def manual_intents_step(names: list[str]) -> str:
    switches = " and ".join(manual for name, _, _, manual in INTENTS if name in names)
    return (f"Developer Portal (discord.com/developers/applications) > your application > Bot > Privileged "
            f"Gateway Intents: turn on {switches}, then Save Changes")


async def configure(rest: Rest, s: Settings, session: aiohttp.ClientSession,
                    force_branding: bool = False) -> tuple[list[str], bool]:
    """Turn on the intents the bot needs; set the description and icon when they are empty (or always,
    with force_branding). Returns lines to print (never secrets) and whether everything went through."""
    app: dict[str, Any] = await rest.request("GET", "/applications/@me")
    lines = [f"Application {app.get('name')!r} (id {app.get('id')}):"]
    ok = True
    flags = int(app.get("flags") or 0)
    new_flags, added = merge_intent_flags(flags)
    if added:
        try:
            await rest.request("PATCH", "/applications/@me", {"flags": new_flags})
            lines.append(f"  intents turned on: {', '.join(added)} (the same as the Bot tab's switches)")
        except DiscordError as e:
            ok = False
            lines.append(f"  Discord did not let the API turn on {', '.join(added)} ({e}).")
            lines.append(f"  Do it by hand: {manual_intents_step(added)}.")
    else:
        lines.append("  intents already on: " + ", ".join(f"{n} ({st})" for n, st in intent_states(flags).items()))

    branding: dict[str, str] = {}
    if force_branding or not str(app.get("description") or "").strip():
        branding["description"] = default_description(s)
    logo, icon_note = s.project.logo_url, ""
    if logo and (force_branding or not app.get("icon")):
        if logo.startswith("/"):
            icon_note = "  icon not set: project.logo_url must be a full https:// address to download it"
        else:
            try:
                branding["icon"] = icon_data_uri(await fetch_logo(session, logo))
            except (ValueError, aiohttp.ClientError, TimeoutError) as e:
                icon_note = f"  icon not set: {e}"
    if branding:
        try:
            await rest.request("PATCH", "/applications/@me", branding)
            if "description" in branding:
                lines.append(f"  description set: {branding['description']}")
            if "icon" in branding:
                lines.append("  icon set from project.logo_url")
        except DiscordError as e:
            ok = False
            lines.append(f"  Discord did not take the description/icon ({e}); the General Information tab has them.")
    kept = [key for key in ("description", "icon") if app.get(key) and key not in branding]
    if kept:
        lines.append(f"  existing {' and '.join(kept)} kept (--force-branding replaces them)")
    if icon_note:
        lines.append(icon_note)
    return lines, ok
