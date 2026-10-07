"""config.toml validation, the .env reader, the session secret and `python -m kit check`."""
from __future__ import annotations

import os
import subprocess
import sys
import tomllib

import pytest

from conftest import APP, BASE_CONFIG, ENV, b58encode, make_settings, merge
from kit.config import ConfigError, load_settings, parse_settings, read_env_file, session_secret

PLACEHOLDER = "0x1234567890abcdef1234567890abcdef12345678"


def problems(raw, env=None, **kw):
    with pytest.raises(ConfigError) as err:
        parse_settings(raw, {**ENV, **(env or {})}, **kw)
    return "\n".join(err.value.problems)


def test_example_config_is_valid():
    s = load_settings(APP / "config.example.toml", {"RPC_ETHEREUM": "https://rpc.example.com"})
    assert s.project.name == "Your Project" and [t.name for t in s.tiers] == ["Holder", "Collector", "Whale"]
    assert [c.wallet_kind for c in s.raffle_chains] == ["evm", "evm", "solana"]
    assert s.support.staff_roles == ("Team", "Mod") and [c.name for c in s.support.categories][0] == "Prize"
    assert [r.name for r in s.self_roles] == ["Giveaway Alerts"] and not s.web_raffles
    assert s.rpc_urls("RPC_ETHEREUM") == ["https://rpc.example.com"]


def test_every_problem_is_reported_at_once():
    raw = merge(BASE_CONFIG, {
        "project": {"color": "lime", "public_url": "https://verify.example.com/path"},
        "collections": [{"name": "A", "chain": "ethereum", "contract": "0xYourContract", "standard": "erc1155"},
                        {"name": "A", "chain": "base", "contract": "0x" + "1" * 40, "standard": "erc999"}],
        "tiers": [{"name": "Holder", "min": 0}],
        "special": [{"name": "S", "role": "Holder", "collection": "Missing", "token_ids": []}],
        "raffles": {"special_bonus": "max", "ticket_cap": "ten"},
        "raffle_chains": [{"name": "Base", "wallet_kind": "btc"}],
        "wallet_kinds": [{"id": "evm", "label": "", "fields": [{"key": "Address", "label": "x" * 50,
                                                                "pattern": "([a-z"}]}],
        "web_raffles": {"enabled": True},
        "typo_section": {},
    })
    raw["raffles"]["tickets_per_tokn"] = 2
    text = problems(raw)
    for bit in ("project.color", "project.public_url", "collections[0].contract", "collections[0].token_ids",
                "collections[1].standard", 'the name "A" is used twice', "tiers[0].min", "special[0].collection",
                "special[0].token_ids", '"Holder" is used by two', "raffles.special_bonus", "raffles.ticket_cap",
                "raffles.tickets_per_tokn: unknown key", "raffle_chains[0].wallet_kind", "wallet_kinds[0].id",
                "wallet_kinds[0].label", "fields[0].key", "fields[0].label", "fields[0].pattern",
                "DISCORD_CLIENT_SECRET", "typo_section: unknown key"):
        assert bit in text, bit


def test_solana_collection_needs_solana_switch():
    raw = merge(BASE_CONFIG, {"verification": {"solana": False}, "collections": [
        {"name": "Sol", "standard": "solana", "contract": b58encode(bytes([14] * 32))}]})
    raw.pop("special")
    assert "verification.solana = true" in problems(raw)


def test_run_needs_token_guild_and_url():
    raw = merge(BASE_CONFIG, {"discord": {"guild_id": 0}, "project": {"public_url": ""}})
    text = problems(raw, env={"DISCORD_TOKEN": ""}, require_runtime=True)
    assert "DISCORD_TOKEN" in text and "guild_id" in text and "public_url" in text


def test_environment_overrides_and_ids_as_text():
    s = make_settings(env={"PUBLIC_URL": "https://other.example.com/", "PORT": "9090"},
                      discord={"guild_id": "1000", "raffle_users": ["77", 78]})
    assert s.project.public_url == "https://other.example.com" and s.web.port == 9090
    assert s.discord.guild_id == 1000 and s.discord.raffle_users == frozenset({77, 78})
    assert s.kind_label("ordinals") == "Bitcoin" and s.kind_label("evm") == "EVM"


def test_token_file(tmp_path):
    (tmp_path / "ids.txt").write_text("# one per line, or commas\n7, 8\n9\n", encoding="utf-8")
    raw = merge(BASE_CONFIG, {"special": [{"name": "Legendary", "role": "Legendary", "collection": "Genesis",
                                           "token_file": "ids.txt"}]})
    s = parse_settings(raw, ENV, tmp_path)
    assert s.specials[0].token_ids == (7, 8, 9)


def test_env_file_and_session_secret(tmp_path):
    (tmp_path / ".env").write_text("# comment\nDISCORD_TOKEN='abc def'\nexport RPC_X=https://a, https://b\n"
                                   "EMPTY=\n", encoding="utf-8")
    env = read_env_file(tmp_path / ".env")
    assert env == {"DISCORD_TOKEN": "abc def", "RPC_X": "https://a, https://b", "EMPTY": ""}
    secret = session_secret({}, tmp_path / "data")
    assert len(secret) == 64 and session_secret({}, tmp_path / "data") == secret  # kept across restarts
    assert session_secret({"SESSION_SECRET": "x" * 40}, tmp_path / "data") == b"x" * 40


def check(tmp_path, config: str, dotenv: str):
    (tmp_path / "config.toml").write_text(config, encoding="utf-8")
    (tmp_path / ".env").write_text(dotenv, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("DISCORD_TOKEN", "RPC_ETHEREUM", "PUBLIC_URL",
                                                            "SESSION_SECRET")}
    env["PYTHONPATH"] = str(APP)
    return subprocess.run([sys.executable, "-m", "kit", "check", "--offline"], cwd=tmp_path, capture_output=True,
                          text=True, env=env, timeout=60)


def test_check_command_never_prints_secrets(tmp_path):
    example = (APP / "config.example.toml").read_text(encoding="utf-8")
    assert tomllib.loads(example)["project"]["name"] == "Your Project"
    real = example.replace(PLACEHOLDER, "0x" + "ab" * 20)
    out = check(tmp_path, real, "DISCORD_TOKEN=super-secret-token\nRPC_ETHEREUM=https://key-in-url.example.com/abc\n")
    assert out.returncode == 0, out.stderr
    assert "bot token set" in out.stdout and "RPC_ETHEREUM (1 URL)" in out.stdout
    assert "super-secret-token" not in out.stdout + out.stderr and "key-in-url" not in out.stdout + out.stderr
    assert "Ready to run: not yet, missing discord.guild_id" in out.stdout


def test_check_refuses_the_placeholder_contract_and_a_short_session_secret(tmp_path):
    example = (APP / "config.example.toml").read_text(encoding="utf-8")
    out = check(tmp_path, example, "DISCORD_TOKEN=x\nSESSION_SECRET=too-short\n")
    assert out.returncode == 1
    assert "collections[0].contract: still the example placeholder" in out.stderr
    assert "SESSION_SECRET: use 32 or more" in out.stderr and "too-short" not in out.stderr
    s = load_settings(APP / "config.example.toml", {})  # the setup commands still accept it
    assert s.collections[0].contract == PLACEHOLDER
