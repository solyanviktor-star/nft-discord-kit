"""Solana holdings through a DAS-capable RPC (`getAssetsByOwner`, Metaplex Digital Asset Standard API).

Only assets in a verified collection grouping count; burnt assets do not.
"""
from __future__ import annotations

from .evm import Rpc, RpcError

PAGE = 1000
MAX_PAGES = 20


async def collection_counts(rpc: Rpc, owner: str, collections: set[str]) -> dict[str, int]:
    """Assets per collection address owned by `owner`. Raises RpcError when the node fails."""
    counts: dict[str, int] = {}
    for page in range(1, MAX_PAGES + 1):
        result = await rpc.request("getAssetsByOwner", {"ownerAddress": owner, "page": page, "limit": PAGE})
        if not isinstance(result, dict):
            raise RpcError("unexpected getAssetsByOwner answer")
        items = result.get("items") or []
        for item in items:
            if item.get("burnt"):
                continue
            for group in item.get("grouping") or []:
                value = group.get("group_value")
                if (group.get("group_key") == "collection" and value in collections
                        and group.get("verified", True) is not False):
                    counts[value] = counts.get(value, 0) + 1
        if len(items) < PAGE:
            break
    return counts
