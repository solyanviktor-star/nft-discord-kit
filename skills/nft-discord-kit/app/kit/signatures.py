"""Wallet sign-in: the messages people sign and the checks on their signatures.

EVM: an EIP-4361 (Sign-In with Ethereum) message signed with personal_sign; the signer is
recovered with eth-account. Contract wallets (Safe and friends) cannot produce such a signature,
so the caller may fall back to EIP-1271 `isValidSignature` over RPC (see `is_valid_signature_call`).
Solana: a plain-text message signed with ed25519 (PyNaCl).
"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone

from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak, to_checksum_address
from nacl.exceptions import CryptoError
from nacl.signing import VerifyKey

from .config import EVM_ADDRESS, SOLANA_ADDRESS

ERC1271_MAGIC = "1626ba7e"  # isValidSignature(bytes32,bytes) selector, also its success value
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def evm_message(domain: str, uri: str, address: str, statement: str, chain_id: int, nonce: str,
                issued_at: float, expires_at: float) -> str:
    """An EIP-4361 message; wallets show it as a sign-in request for `domain`."""
    return (f"{domain} wants you to sign in with your Ethereum account:\n"
            f"{to_checksum_address(address)}\n\n{statement}\n\n"
            f"URI: {uri}\nVersion: 1\nChain ID: {chain_id}\nNonce: {nonce}\n"
            f"Issued At: {iso(issued_at)}\nExpiration Time: {iso(expires_at)}")


def solana_message(domain: str, address: str, statement: str, nonce: str, issued_at: float,
                   expires_at: float) -> str:
    return (f"{domain} wants you to sign in with your Solana account:\n{address}\n\n{statement}\n\n"
            f"Nonce: {nonce}\nIssued At: {iso(issued_at)}\nExpiration Time: {iso(expires_at)}")


def evm_recover(message: str, signature: str) -> str | None:
    """The lower-case address that personal_sign-ed `message`, or None for a malformed signature."""
    try:
        return Account.recover_message(encode_defunct(text=message), signature=signature).lower()
    except Exception:  # noqa: BLE001 - eth-account raises several unrelated types for bad input
        return None


def eip191_hash(message: str) -> bytes:
    """The hash personal_sign signs; contract wallets are asked about this hash."""
    data = message.encode("utf-8")
    return keccak(b"\x19Ethereum Signed Message:\n" + str(len(data)).encode("ascii") + data)


def is_valid_signature_call(msg_hash: bytes, signature: bytes) -> str:
    """eth_call data for EIP-1271 isValidSignature(bytes32 hash, bytes signature)."""
    sig = signature.hex()
    return ("0x" + ERC1271_MAGIC + msg_hash.hex() + "%064x" % 64 + "%064x" % len(signature)
            + sig + "0" * (-len(sig) % 64))


def is_magic(result: str | None) -> bool:
    """True when an isValidSignature call returned the EIP-1271 success value."""
    return bool(result) and result.removeprefix("0x")[:8].lower() == ERC1271_MAGIC


def b58decode(text: str) -> bytes:
    """Base58 (Bitcoin alphabet) to bytes; ValueError on a bad character."""
    num = 0
    for ch in text:
        num = num * 58 + _B58.index(ch)
    raw = num.to_bytes((num.bit_length() + 7) // 8, "big")
    return b"\x00" * (len(text) - len(text.lstrip("1"))) + raw


def solana_verify(address: str, message: str, signature: bytes) -> bool:
    """Does `signature` (64 bytes) sign `message` with the ed25519 key `address`?"""
    try:
        key = b58decode(address)
        if len(key) != 32:
            return False
        VerifyKey(key).verify(message.encode("utf-8"), signature)
        return True
    except (ValueError, TypeError, CryptoError):
        return False


def normalize_address(kind: str, address: str) -> str | None:
    """EVM addresses in lower case, Solana addresses as they are; None when malformed."""
    a = str(address or "").strip()
    if kind == "evm":
        return a.lower() if EVM_ADDRESS.match(a) else None
    if kind == "solana" and SOLANA_ADDRESS.match(a):
        try:
            return a if len(b58decode(a)) == 32 else None
        except ValueError:
            return None
    return None


def decode_signature(kind: str, text: str) -> bytes | None:
    """EVM signatures arrive as 0x-hex, Solana signatures as base64; None when malformed."""
    text = str(text or "").strip()
    try:
        if kind == "evm":
            raw = bytes.fromhex(text.removeprefix("0x"))
        else:
            raw = base64.b64decode(text, validate=True)
    except (ValueError, binascii.Error):
        return None
    return raw if 64 <= len(raw) <= 4096 else None
