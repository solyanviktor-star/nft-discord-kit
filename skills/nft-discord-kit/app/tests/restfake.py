"""A fake Discord REST API for the setup commands; it records every request.

Just enough of Discord: the bot user, its application (only the limited intent flags may be edited,
like the real API), its servers, roles, channels, permission overwrites, panel messages, and a logo
file to download. `rest.API` is pointed at it by the `discord_api` fixture in conftest.py.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from aiohttp import web

from kit import application

TEMPLATE = str(Path(__file__).resolve().parents[1] / "templates" / "server.toml")
PNG = b"\x89PNG\r\n\x1a\n" + bytes(64)


def args(command: str, **kw: Any) -> argparse.Namespace:
    """What argparse would give kit.setup.dispatch for this command."""
    defaults = {"template": TEMPLATE, "yes": False, "admin": False, "config": "config.toml", "wait": False,
                "timeout": 600, "pick": None, "force_branding": False}
    return argparse.Namespace(command=command, **{**defaults, **kw})


class FakeDiscord:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, object]] = []
        self.ids = iter(range(5000, 9000))
        self.url = ""  # set by the fixture: where this fake listens
        self.guild = {"id": "1000", "name": "Fresh Server", "verification_level": 0, "default_message_notifications": 0,
                      "explicit_content_filter": 0,
                      "roles": [{"id": "1000", "name": "@everyone", "position": 0, "permissions": "0"},
                                {"id": "50", "name": "kit-bot", "position": 1, "managed": True,
                                 "permissions": "8"}]}  # invited with --admin
        self.channels = [{"id": "60", "name": "Text Channels", "type": 4, "permission_overwrites": []},
                         {"id": "61", "name": "general", "type": 0, "parent_id": "60", "permission_overwrites": []},
                         {"id": "62", "name": "Voice Channels", "type": 4, "permission_overwrites": []},
                         {"id": "63", "name": "General", "type": 2, "parent_id": "62", "permission_overwrites": []}]
        self.messages: dict[str, dict] = {}
        # the application: intents on and branding set (tests that need a fresh one change these)
        self.application = {"id": "1234", "name": "Kit Test", "description": "Ours already", "icon": "a1b2",
                            "flags": application.MEMBERS_LIMITED | application.CONTENT_LIMITED}
        self.refuse_flags = False
        self.servers = [{"id": "1000", "name": "Fresh Server"}]
        self.empty_polls = 0  # /users/@me/guilds answers [] this many times first (the invite is not done yet)
        self.files = {"/logo.png": PNG, "/big.png": PNG + bytes(application.MAX_ICON_BYTES),
                      "/page.html": b"<html>not an image</html>"}
        self.valid_tokens: set[str] | None = None  # when set: other Authorization headers get a 401

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self.handle)
        return app

    async def handle(self, request: web.Request) -> web.Response:
        body = await request.json() if request.can_read_body else None
        method, path = request.method, request.path
        self.requests.append((method, path, body))
        g = "/guilds/1000"
        if method == "GET" and path in self.files:
            return web.Response(body=self.files[path], content_type="application/octet-stream")
        token = request.headers.get("Authorization", "").removeprefix("Bot ")
        if self.valid_tokens is not None and token not in self.valid_tokens:
            return web.json_response({"code": 0, "message": "401: Unauthorized"}, status=401)
        if method == "GET" and path == "/users/@me/guilds":
            if self.empty_polls:
                self.empty_polls -= 1
                return web.json_response([])
            return web.json_response(self.servers)
        if method == "PATCH" and path == "/applications/@me":
            return self.edit_application(body)
        if method == "GET":
            routes = {"/users/@me": {"id": "1", "username": "kit-bot"}, "/applications/@me": self.application,
                      g: self.guild, g + "/channels": self.channels, g + "/members/1": {"roles": ["50"]}}
            return web.json_response(routes[path]) if path in routes else web.json_response({"code": 0}, status=404)
        if method == "POST" and path == g + "/roles":
            for role in self.guild["roles"]:
                role["position"] += role["position"] > 0  # Discord puts a new role right above @everyone
            role = {"id": str(next(self.ids)), "position": 1, **body}
            self.guild["roles"].append(role)
            return web.json_response(role)
        if method == "POST" and path == g + "/channels":
            channel = {"id": str(next(self.ids)), **body}
            self.channels.append(channel)
            return web.json_response(channel)
        if method == "PUT" and path.startswith("/channels/") and "/permissions/" in path:
            cid, target = path.split("/")[2], path.split("/")[4]
            channel = next(c for c in self.channels if c["id"] == cid)
            channel.setdefault("permission_overwrites", []).append({"id": target, **body})
            return web.Response(status=204)
        if method == "PATCH" and path == g:
            self.guild.update(body)
            return web.json_response(self.guild)
        if method == "POST" and path.endswith("/messages"):
            mid = str(next(self.ids))
            self.messages[mid] = body
            return web.json_response({"id": mid})
        if method == "PATCH" and "/messages/" in path:
            mid = path.rsplit("/", 1)[1]
            if mid not in self.messages:
                return web.json_response({"code": 10008, "message": "Unknown Message"}, status=404)
            self.messages[mid] = body
            return web.json_response({"id": mid})
        return web.json_response({"code": 0, "message": "not faked"}, status=405)

    def edit_application(self, body: dict) -> web.Response:
        """Like Discord: only the limited intent flags may be sent; every other bit stays as it is."""
        if "flags" in body:
            if self.refuse_flags or body["flags"] & ~application.EDITABLE_FLAGS:
                return web.json_response({"code": 50035, "message": "Invalid Form Body"}, status=400)
            kept = self.application["flags"] & ~application.EDITABLE_FLAGS
            self.application["flags"] = kept | body["flags"]
        if "description" in body:
            self.application["description"] = body["description"]
        if "icon" in body:
            self.application["icon"] = "new-icon-hash"
        return web.json_response(self.application)
