"""python -m kit run | check

  run     start the Discord bot and the verification website (one long-running process)
  check   validate config.toml and .env and print a summary; secrets are never printed

The server builder is a separate command: python -m kit.setup --help
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from .config import ConfigError, Settings, load_env_file, load_settings


def data_dir() -> Path:
    return Path(os.environ.get("KIT_DATA_DIR") or "data")


def summary(s: Settings) -> str:
    """What the config says, for a person to confirm. Shows which secrets are set, never their values."""
    def yes(flag: bool) -> str:
        return "set" if flag else "NOT SET"

    cols = []
    for c in s.collections:
        urls = len(s.rpc_urls(c.rpc_env))
        cols.append(f"  - {c.name}: {c.standard} on {c.chain}, {c.contract[:8]}...{c.contract[-4:]}, "
                    f"{c.rpc_env} ({urls} URL{'' if urls == 1 else 's'}{'' if urls else ': set it in .env'})"
                    + (f", role {c.role}" if c.role else ""))
    r = s.raffles
    missing = [m for m, ok in (("DISCORD_TOKEN", s.discord_token), ("discord.guild_id", s.discord.guild_id),
                               ("project.public_url / PUBLIC_URL", s.project.public_url)) if not ok]
    return "\n".join([
        f"Project:     {s.project.name}  (public URL: {s.project.public_url or 'not set yet'})",
        f"Discord:     server {s.discord.guild_id or 'not set (python -m kit.setup guilds)'}; "
        f"bot token {yes(bool(s.discord_token))}; OAuth login {'on' if s.oauth_enabled else 'off'}"
        + (", auto-join on" if s.auto_join else ""),
        "Collections:", *cols,
        "Tiers:       " + ", ".join(f"{t.name} {t.min}+" for t in s.tiers),
        "Specials:    " + (", ".join(f"{sp.name} ({len(sp.token_ids)} tokens, role {sp.role}, +{sp.bonus_tickets})"
                                     for sp in s.specials) or "none"),
        "Raffles:     " + ", ".join(f"{c.name} ({c.wallet_kind})" for c in s.raffle_chains)
        + f"; {r.tickets_per_token} ticket/NFT up to {r.ticket_cap}, bonus {r.special_bonus}; "
        + ("holding required" if r.require_holding else "holding not required"),
        f"Staff:       {', '.join(s.discord.staff_roles)}; raffle managers {', '.join(s.discord.raffle_manager_roles)}",
        f"Modules:     tickets {'on' if s.support.enabled else 'off'}; self-roles "
        f"{', '.join(x.name for x in s.self_roles) or 'off'}; website raffles {'on' if s.web_raffles else 'off'}; "
        f"Solana wallets {'on' if s.solana else 'off'}",
        "Ready to run: " + ("yes" if not missing else "not yet, missing " + ", ".join(missing)),
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m kit", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["run", "check"])
    parser.add_argument("--config", default=os.environ.get("KIT_CONFIG", "config.toml"), help="default: config.toml")
    args = parser.parse_args(argv)
    load_env_file(Path(".env"))
    try:
        settings = load_settings(Path(args.config), require_runtime=args.command == "run")
    except ConfigError as e:
        print("Config problems:\n" + "\n".join(f"  - {p}" for p in e.problems), file=sys.stderr)
        return 1
    if args.command == "check":
        print(summary(settings))
        return 0
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    import discord  # only `run` needs discord.py

    from .app import run
    try:
        asyncio.run(run(settings, data_dir()))
    except discord.LoginFailure:
        print("Discord rejected DISCORD_TOKEN. Reset the token (Developer Portal > Bot) and update .env.", file=sys.stderr)
        return 1
    except discord.PrivilegedIntentsRequired:
        print("Turn on SERVER MEMBERS INTENT (Developer Portal > Bot > Privileged Gateway Intents), then restart.",
              file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
