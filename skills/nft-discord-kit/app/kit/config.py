"""Configuration: `config.toml` (edited by people) plus secrets from the environment (`.env`).

`parse_settings` reads every section, collects every problem it finds and raises one
`ConfigError` that lists all of them, so the file can be fixed in one pass. Unknown keys
are reported too: a typo must never fall back to a default silently.
"""
from __future__ import annotations

import os
import re
import secrets
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"
# The example's placeholder and the zero address: `kit check` and `run` refuse them (an empty answer from
# a non-contract must never be read as "everyone holds zero").
PLACEHOLDER_CONTRACTS = frozenset({"0x1234567890abcdef1234567890abcdef12345678", "0x" + "0" * 40})
EVM_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
SOLANA_ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
BUILTIN_WALLET_KINDS = ("evm", "solana")
DEFAULT_CHANNELS = {
    "verify": "verify",
    "giveaways": "giveaways",
    "winners": "giveaways-winners",
    "tickets": "tickets",
    "ticket_log": "ticket-log",
    "self_roles": "claim-roles",
}
_PUBLIC_URL = re.compile(r"^(https://[A-Za-z0-9.-]+(:\d+)?|http://localhost(:\d+)?)$")


class ConfigError(Exception):
    """Every problem found in the configuration, one per line."""

    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Project:
    name: str
    color: int
    logo_url: str
    public_url: str  # "" until the site is deployed


@dataclass(frozen=True)
class DiscordSettings:
    guild_id: int
    application_id: int
    staff_roles: tuple[str, ...]
    raffle_manager_roles: tuple[str, ...]
    raffle_users: frozenset[int]


@dataclass(frozen=True)
class WebSettings:
    host: str
    port: int
    trust_proxy: bool
    rate_limit_per_minute: int


@dataclass(frozen=True)
class Collection:
    name: str
    chain: str
    standard: str  # "erc721" | "erc1155" | "solana"
    contract: str  # EVM contract (lower case) or the Solana collection address
    rpc_env: str  # env var that holds one or more RPC URLs
    token_ids: tuple[int, ...] = ()
    role: str = ""
    multicall: str = MULTICALL3

    @property
    def is_evm(self) -> bool:
        return self.standard != "solana"


@dataclass(frozen=True)
class Tier:
    name: str
    min: int


@dataclass(frozen=True)
class Special:
    name: str
    role: str
    collection: str
    token_ids: tuple[int, ...]
    bonus_tickets: int = 0


@dataclass(frozen=True)
class RaffleRules:
    require_holding: bool = True
    tickets_per_token: int = 1
    ticket_cap: int = 10
    special_bonus: str = "best"  # "best" | "sum"
    alert_role: str = "Giveaway Alerts"
    eligible_roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class RaffleChain:
    name: str
    wallet_kind: str


@dataclass(frozen=True)
class WalletField:
    key: str
    label: str
    pattern: str
    placeholder: str = ""


@dataclass(frozen=True)
class WalletKind:
    id: str
    label: str
    fields: tuple[WalletField, ...]


@dataclass(frozen=True)
class TicketCategory:
    emoji: str
    name: str


@dataclass(frozen=True)
class SupportSettings:
    enabled: bool
    staff_roles: tuple[str, ...]
    categories: tuple[TicketCategory, ...]
    max_open: int
    archive_hours: int
    greeting: str


@dataclass(frozen=True)
class SelfRole:
    name: str
    emoji: str = ""
    description: str = ""


@dataclass(frozen=True)
class Settings:
    project: Project
    discord: DiscordSettings
    channels: Mapping[str, str]
    web: WebSettings
    sync_minutes: int
    chain_id: int
    solana: bool
    collections: tuple[Collection, ...]
    tiers: tuple[Tier, ...]  # ascending by `min`
    specials: tuple[Special, ...]
    raffles: RaffleRules
    raffle_chains: tuple[RaffleChain, ...]
    wallet_kinds: Mapping[str, WalletKind]  # custom kinds only, by id
    support: SupportSettings
    self_roles: tuple[SelfRole, ...]  # empty when the module is off
    web_raffles: bool
    web_raffles_recent_days: int
    auto_join: bool
    discord_token: str = field(default="", repr=False)
    client_secret: str = field(default="", repr=False)
    env: Mapping[str, str] = field(default_factory=dict, repr=False)

    @property
    def oauth_enabled(self) -> bool:
        return bool(self.client_secret)

    def rpc_urls(self, env_name: str) -> list[str]:
        """RPC URLs from one env var (comma or space separated). Never log them: they may hold API keys."""
        raw = self.env.get(env_name, "")
        return [u for u in re.split(r"[\s,]+", raw) if u.startswith(("https://", "http://"))]

    def holder_roles(self) -> list[str]:
        """Every role the kit manages from holdings: tiers, collection roles, special roles."""
        return ([t.name for t in self.tiers] + [c.role for c in self.collections if c.role]
                + [s.role for s in self.specials])

    def chain(self, name: str) -> RaffleChain | None:
        return next((c for c in self.raffle_chains if c.name == name), None)

    def kind_label(self, kind: str) -> str:
        if kind in self.wallet_kinds:
            return self.wallet_kinds[kind].label
        return {"evm": "EVM", "solana": "Solana"}.get(kind, kind)


_TYPE_NAMES = {str: "a string", int: "a whole number", bool: "true or false", list: "a list", dict: "a table"}


class _Sec:
    """One TOML table: typed getters that record problems instead of raising."""

    def __init__(self, data: Any, where: str, problems: list[str]):
        if not isinstance(data, dict):
            problems.append(f"{where}: expected a table")
            data = {}
        self.data, self.where, self.problems = data, where, problems
        self.seen: set[str] = set()

    def at(self, key: str) -> str:
        return f"{self.where}.{key}" if self.where else key

    def bad(self, key: str, message: str) -> None:
        self.problems.append(f"{self.at(key)}: {message}")

    def get(self, key: str, default: Any, kind: type) -> Any:
        self.seen.add(key)
        if key not in self.data:
            return default
        value = self.data[key]
        if isinstance(value, kind) and not (kind is int and isinstance(value, bool)):
            return value.strip() if kind is str else value
        self.bad(key, f"expected {_TYPE_NAMES[kind]}")
        return default

    def strs(self, key: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
        value = self.get(key, None, list)
        if value is None:
            return default
        if not all(isinstance(v, str) for v in value):
            self.bad(key, "expected a list of strings")
            return default
        return tuple(v.strip() for v in value if v.strip())

    def ids(self, key: str) -> tuple[int, ...]:
        """Whole numbers, also accepted as digit strings (Discord ids are often pasted as text)."""
        value = self.get(key, [], list)
        out = []
        for v in value:
            if isinstance(v, str) and v.strip().isdigit():
                v = int(v.strip())
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                self.bad(key, "expected a list of whole numbers")
                return ()
            out.append(v)
        return tuple(out)

    def snowflake(self, key: str) -> int:
        """A Discord id written as a number or as a digit string; 0 when absent."""
        self.seen.add(key)
        value = self.data.get(key, 0)
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            self.bad(key, "expected a Discord id (digits)")
            return 0
        return value

    def table(self, key: str) -> _Sec:
        return _Sec(self.get(key, {}, dict), self.at(key), self.problems)

    def tables(self, key: str) -> list[_Sec]:
        return [_Sec(v, f"{self.at(key)}[{i}]", self.problems) for i, v in enumerate(self.get(key, [], list))]

    def finish(self) -> None:
        for key in sorted(set(self.data) - self.seen):
            self.bad(key, "unknown key")


def default_rpc_env(chain: str) -> str:
    return "RPC_" + re.sub(r"[^A-Z0-9]+", "_", chain.upper()).strip("_")


def _color(value: str, sec: _Sec) -> int:
    if not re.fullmatch(r"#?[0-9a-fA-F]{6}", value):
        sec.bad("color", "expected a hex color like #5865F2")
        return 0x5865F2
    return int(value.lstrip("#"), 16)


def _read_token_file(path: Path, sec: _Sec) -> tuple[int, ...]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        sec.bad("token_file", f"cannot read {path}")
        return ()
    parts = [p for p in re.split(r"[\s,]+", re.sub(r"#.*", "", text)) if p]
    if not all(p.isdigit() for p in parts):
        sec.bad("token_file", "expected token ids (digits) separated by spaces, commas or new lines")
        return ()
    return tuple(int(p) for p in parts)


def _collections(root: _Sec, solana: bool) -> tuple[Collection, ...]:
    out: list[Collection] = []
    for s in root.tables("collections"):
        standard = s.get("standard", "erc721", str).lower()
        chain = s.get("chain", "solana" if standard == "solana" else "", str).lower()
        col = Collection(
            name=s.get("name", "", str), chain=chain, standard=standard,
            contract=s.get("contract", "", str), rpc_env=s.get("rpc_env", "", str) or default_rpc_env(chain),
            token_ids=s.ids("token_ids"), role=s.get("role", "", str), multicall=s.get("multicall", MULTICALL3, str))
        if not col.name:
            s.bad("name", "required")
        if not chain:
            s.bad("chain", "required, e.g. \"ethereum\" or \"base\"")
        if standard not in ("erc721", "erc1155", "solana"):
            s.bad("standard", "expected \"erc721\", \"erc1155\" or \"solana\"")
        elif standard == "solana":
            if not SOLANA_ADDRESS.match(col.contract):
                s.bad("contract", "expected the Solana collection address (base58)")
            if not solana:
                s.bad("standard", "Solana collections need verification.solana = true")
        else:
            if not EVM_ADDRESS.match(col.contract):
                s.bad("contract", "expected a 0x address with 40 hex characters")
            if standard == "erc1155" and not col.token_ids:
                s.bad("token_ids", "an erc1155 collection needs the token ids to count")
            if col.multicall and not EVM_ADDRESS.match(col.multicall):
                s.bad("multicall", "expected a 0x address, or \"\" to use plain eth_call")
            col = replace(col, contract=col.contract.lower())
        s.finish()
        out.append(col)
    if not out:
        root.problems.append("collections: add at least one [[collections]] entry")
    names = [c.name for c in out]
    for name in {n for n in names if names.count(n) > 1}:
        root.problems.append(f"collections: the name \"{name}\" is used twice")
    return tuple(out)


def _tiers(root: _Sec) -> tuple[Tier, ...]:
    if "tiers" not in root.data:
        root.seen.add("tiers")
        return (Tier("Holder", 1), Tier("Collector", 3), Tier("Whale", 10))
    out = []
    for s in root.tables("tiers"):
        tier = Tier(s.get("name", "", str), s.get("min", 0, int))
        if not tier.name:
            s.bad("name", "required")
        if tier.min < 1:
            s.bad("min", "expected 1 or more")
        s.finish()
        out.append(tier)
    if not out:
        root.problems.append("tiers: add at least one [[tiers]] entry")
    if len({t.min for t in out}) != len(out):
        root.problems.append("tiers: two tiers have the same min")
    return tuple(sorted(out, key=lambda t: t.min))


def _specials(root: _Sec, collections: tuple[Collection, ...], base_dir: Path) -> tuple[Special, ...]:
    evm = {c.name for c in collections if c.is_evm}
    out = []
    for s in root.tables("special"):
        ids = s.ids("token_ids")
        token_file = s.get("token_file", "", str)
        if token_file:
            ids = ids + _read_token_file(base_dir / token_file, s)
        sp = Special(name=s.get("name", "", str), role=s.get("role", "", str),
                     collection=s.get("collection", "", str), token_ids=tuple(sorted(set(ids))),
                     bonus_tickets=s.get("bonus_tickets", 0, int))
        if not sp.name:
            s.bad("name", "required")
        if not sp.role:
            s.bad("role", "required (the role given to holders of these tokens)")
        if sp.collection not in evm:
            s.bad("collection", "expected the name of an EVM collection from [[collections]]")
        if not sp.token_ids:
            s.bad("token_ids", "list the token ids (inline or with token_file)")
        if sp.bonus_tickets < 0:
            s.bad("bonus_tickets", "expected 0 or more")
        s.finish()
        out.append(sp)
    return tuple(out)


def _wallet_kinds(root: _Sec) -> dict[str, WalletKind]:
    out: dict[str, WalletKind] = {}
    for s in root.tables("wallet_kinds"):
        kid = s.get("id", "", str)
        fields = []
        for f in s.tables("fields"):
            wf = WalletField(f.get("key", "", str), f.get("label", "", str), f.get("pattern", "", str),
                             f.get("placeholder", "", str))
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,30}", wf.key):
                f.bad("key", "expected a short lower-case key like \"address\"")
            if not 1 <= len(wf.label) <= 45:
                f.bad("label", "expected 1 to 45 characters (a Discord limit)")
            try:
                re.compile(wf.pattern)
            except re.error as e:
                f.bad("pattern", f"not a valid regular expression ({e})")
            f.finish()
            fields.append(wf)
        kind = WalletKind(kid, s.get("label", "", str), tuple(fields))
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,20}", kid) or kid in BUILTIN_WALLET_KINDS:
            s.bad("id", "expected a short lower-case id that is not \"evm\" or \"solana\"")
        if not kind.label:
            s.bad("label", "required")
        if not 1 <= len(fields) <= 5:
            s.bad("fields", "expected 1 to 5 fields (a Discord modal holds five)")
        if kid in out:
            s.bad("id", f"\"{kid}\" is defined twice")
        s.finish()
        out[kid] = kind
    return out


def parse_settings(raw: Mapping[str, Any], env: Mapping[str, str], base_dir: Path | None = None,
                   require_runtime: bool = False, strict: bool = False) -> Settings:
    """Validate a parsed config.toml. `strict` (kit check, run) also refuses the example's placeholder
    contract; `require_runtime` (run) also demands the token, the server id and the public URL."""
    problems: list[str] = []
    root = _Sec(dict(raw), "", problems)
    base_dir = base_dir or Path.cwd()

    p = root.table("project")
    configured_url = p.get("public_url", "", str)
    project = Project(p.get("name", "", str), _color(p.get("color", "#5865F2", str), p), p.get("logo_url", "", str),
                      (env.get("PUBLIC_URL", "").strip() or configured_url).rstrip("/"))
    if not project.name:
        p.bad("name", "required")
    if project.logo_url and not project.logo_url.startswith(("https://", "/")):
        p.bad("logo_url", "expected an https:// URL")
    if project.public_url and not _PUBLIC_URL.match(project.public_url):
        p.bad("public_url", "expected https://your.domain (no path), or http://localhost:PORT for local tests")
    p.finish()

    d = root.table("discord")
    discord = DiscordSettings(d.snowflake("guild_id"), d.snowflake("application_id"),
                              d.strs("staff_roles", ("Team", "Mod")),
                              d.strs("raffle_manager_roles", ("Raffle Manager",)), frozenset(d.ids("raffle_users")))
    d.finish()

    c = root.table("channels")
    channels = {k: c.get(k, v, str) or v for k, v in DEFAULT_CHANNELS.items()}
    c.finish()

    w = root.table("web")
    port = w.get("port", 8080, int)
    env_port = env.get("PORT", "").strip()  # hosting platforms (Railway, Render, ...) pass the port here
    if env_port:
        if env_port.isdigit():
            port = int(env_port)
        else:
            problems.append("PORT: expected a number")
    web = WebSettings(w.get("host", "", str), port, w.get("trust_proxy", True, bool),
                      w.get("rate_limit_per_minute", 30, int))
    if not 1 <= web.port <= 65535:
        w.bad("port", "expected 1-65535")
    if web.rate_limit_per_minute < 1:
        w.bad("rate_limit_per_minute", "expected 1 or more")
    w.finish()

    v = root.table("verification")
    sync_minutes, chain_id, solana = v.get("sync_minutes", 30, int), v.get("chain_id", 1, int), v.get("solana", False, bool)
    if sync_minutes < 1:
        v.bad("sync_minutes", "expected 1 or more")
    v.finish()

    collections = _collections(root, solana)
    tiers = _tiers(root)
    specials = _specials(root, collections, base_dir)
    managed = [t.name for t in tiers] + [x.role for x in collections if x.role] + [s.role for s in specials]
    for role in sorted({r for r in managed if managed.count(r) > 1}):
        problems.append(f"roles: \"{role}\" is used by two tiers/collections/specials; give each its own role")

    r = root.table("raffles")
    rules = RaffleRules(r.get("require_holding", True, bool), r.get("tickets_per_token", 1, int),
                        r.get("ticket_cap", 10, int), r.get("special_bonus", "best", str),
                        r.get("alert_role", "Giveaway Alerts", str), r.strs("eligible_roles"))
    if rules.tickets_per_token < 1:
        r.bad("tickets_per_token", "expected 1 or more")
    if rules.ticket_cap < 1:
        r.bad("ticket_cap", "expected 1 or more")
    if rules.special_bonus not in ("best", "sum"):
        r.bad("special_bonus", "expected \"best\" or \"sum\"")
    r.finish()

    kinds = _wallet_kinds(root)
    chains = []
    for s in root.tables("raffle_chains"):
        rc = RaffleChain(s.get("name", "", str), s.get("wallet_kind", "evm", str))
        if not rc.name:
            s.bad("name", "required")
        if rc.wallet_kind not in BUILTIN_WALLET_KINDS and rc.wallet_kind not in kinds:
            s.bad("wallet_kind", "expected \"evm\", \"solana\" or the id of a [[wallet_kinds]] entry")
        s.finish()
        chains.append(rc)
    if "raffle_chains" not in root.data:
        chains = [RaffleChain("Ethereum", "evm")]
    if not 1 <= len(chains) <= 25 or len({x.name for x in chains}) != len(chains):
        problems.append("raffle_chains: expected 1 to 25 entries with different names")

    sp = root.table("support")
    cats = []
    for s in sp.tables("categories"):
        cats.append(TicketCategory(s.get("emoji", "", str), s.get("name", "", str)))
        if not cats[-1].name:
            s.bad("name", "required")
        s.finish()
    if "categories" not in sp.data:
        cats = [TicketCategory("\U0001F381", "Prize"), TicketCategory("\U0001F6E0", "Support"),
                TicketCategory("\U0001F91D", "Partnership"), TicketCategory("❓", "Other")]
    support = SupportSettings(sp.get("enabled", True, bool), sp.strs("staff_roles", discord.staff_roles), tuple(cats),
                              sp.get("max_open", 1, int), sp.get("archive_hours", 72, int),
                              sp.get("greeting", "Thanks! Describe your question below — the team will reply "
                                                 "as soon as possible.", str))
    if not 1 <= len(cats) <= 5 or len({x.name.casefold() for x in cats}) != len(cats):
        sp.bad("categories", "expected 1 to 5 categories with different names")
    if support.max_open < 1:
        sp.bad("max_open", "expected 1 or more")
    if support.archive_hours < 1:
        sp.bad("archive_hours", "expected 1 or more")
    sp.finish()

    sr = root.table("self_roles")
    enabled = sr.get("enabled", True, bool)
    roles = []
    for s in sr.tables("roles"):
        roles.append(SelfRole(s.get("name", "", str), s.get("emoji", "", str), s.get("description", "", str)))
        if not roles[-1].name:
            s.bad("name", "required")
        s.finish()
    if "roles" not in sr.data:
        roles = [SelfRole("Giveaway Alerts", "\U0001F514", "Get pinged when a new giveaway starts")]
    if len(roles) > 20:
        sr.bad("roles", "expected at most 20 roles")
    sr.finish()

    o = root.table("oauth")
    auto_join = o.get("auto_join", False, bool)
    o.finish()
    wr = root.table("web_raffles")
    web_raffles, recent_days = wr.get("enabled", False, bool), wr.get("recent_days", 7, int)
    wr.finish()

    token = env.get("DISCORD_TOKEN", "").strip()
    client_secret = env.get("DISCORD_CLIENT_SECRET", "").strip()
    if (web_raffles or auto_join) and not client_secret:
        problems.append("web_raffles / oauth.auto_join need Discord OAuth: set DISCORD_CLIENT_SECRET in .env")
    if 0 < len(env.get("SESSION_SECRET", "").strip()) < 32:
        problems.append("SESSION_SECRET: use 32 or more random characters, or leave it empty to keep a generated one "
                        "in data/ (a short one would be ignored and a new secret made on every deploy)")
    if strict:
        for i, c in enumerate(collections):
            if c.contract in PLACEHOLDER_CONTRACTS:
                problems.append(f"collections[{i}].contract: still the example placeholder; put {c.name}'s real "
                                "contract address here")
    if require_runtime:
        if not token:
            problems.append("DISCORD_TOKEN is not set (put it in .env)")
        if not discord.guild_id:
            problems.append("discord.guild_id is not set (run: python -m kit.setup guilds)")
        if not project.public_url:
            problems.append("project.public_url (or PUBLIC_URL) is not set")
    root.finish()
    if problems:
        raise ConfigError(problems)
    return Settings(project=project, discord=discord, channels=channels, web=web, sync_minutes=sync_minutes,
                    chain_id=chain_id, solana=solana, collections=collections, tiers=tiers, specials=specials,
                    raffles=rules, raffle_chains=tuple(chains), wallet_kinds=kinds, support=support,
                    self_roles=tuple(roles) if enabled else (), web_raffles=web_raffles,
                    web_raffles_recent_days=recent_days, auto_join=auto_join, discord_token=token,
                    client_secret=client_secret, env=dict(env))


def load_settings(path: Path, env: Mapping[str, str] | None = None, require_runtime: bool = False,
                  strict: bool = False) -> Settings:
    """Read and validate a config file; raises ConfigError with every problem."""
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError([f"{path}: not found (copy config.example.toml to config.toml)"]) from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError([f"{path}: {e}"]) from None
    return parse_settings(raw, os.environ if env is None else env, path.parent, require_runtime, strict)


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines from a .env file; `#` comments; optional quotes. A missing file gives {}."""
    out: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip().removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        out[key] = value
    return out


def load_env_file(path: Path) -> None:
    """Copy .env values into os.environ; real environment variables win."""
    for key, value in read_env_file(path).items():
        os.environ.setdefault(key, value)


def session_secret(env: Mapping[str, str], data_dir: Path) -> bytes:
    """SESSION_SECRET from the environment, else a random one kept in data/session_secret (mode 600)."""
    value = env.get("SESSION_SECRET", "").strip()
    if len(value) >= 32:
        return value.encode()
    path = data_dir / "session_secret"
    try:
        stored = path.read_text(encoding="utf-8").strip()
        if len(stored) >= 32:
            return stored.encode()
    except OSError:
        pass
    data_dir.mkdir(parents=True, exist_ok=True)
    value = secrets.token_hex(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(value)
    return value.encode()
