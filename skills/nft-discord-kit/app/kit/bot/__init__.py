"""The Discord layer (discord.py). Everything here is thin: the rules live in `kit.service` and friends."""

from .client import KitBot

__all__ = ["KitBot"]
