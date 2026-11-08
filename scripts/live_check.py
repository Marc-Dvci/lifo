"""Check the two testnet contracts and the public page from public endpoints. Writes nothing.

    python scripts/live_check.py

For `launch.lifo.testnet` and `demo.lifo.testnet` it reads the account from the public RPC and checks that the code
hash is the SHA-256 of the reviewed wasm (the hash written in contract/README.md) and that the access key list is
empty. Then it fetches the page's state route for the demo contract. Exit code 1 if any check fails.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RPC = "https://test.rpc.fastnear.com"
PAGE = "https://lifo-amt.pages.dev"
CONTRACTS = ["launch.lifo.testnet", "demo.lifo.testnet"]
AGENT = {"User-Agent": "lifo-live-check/1.0 (+https://github.com/Marc-Dvci/lifo)"}
ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def base58(raw: bytes) -> str:
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = ALPHABET[r] + out
    return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + out


def expected_code_hash() -> str:
    text = (ROOT / "contract" / "README.md").read_text(encoding="utf-8")
    match = re.search(r"`target/near/lifo/lifo\.wasm` \| [\d,]+ bytes \| `([0-9a-f]{64})`", text)
    if match is None:
        raise SystemExit("contract/README.md has no wasm hash row")
    return base58(bytes.fromhex(match.group(1)))


def rpc(params: dict[str, str]) -> dict[str, object]:
    body = json.dumps({"jsonrpc": "2.0", "id": "live", "method": "query", "params": params}).encode()
    request = urllib.request.Request(RPC, body, {**AGENT, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        answer: dict[str, object] = json.load(response)
    if "result" not in answer:
        raise RuntimeError(f"RPC answered without a result: {str(answer)[:200]}")
    result = answer["result"]
    assert isinstance(result, dict)
    return result


def main() -> int:
    wanted = expected_code_hash()
    failed = False
    for account in CONTRACTS:
        info = rpc({"request_type": "view_account", "finality": "final", "account_id": account})
        keys = rpc({"request_type": "view_access_key_list", "finality": "final", "account_id": account})["keys"]
        assert isinstance(keys, list)
        same = info["code_hash"] == wanted
        print(
            f"{account}: code hash {info['code_hash']} ({'matches' if same else 'DIFFERS from'} the reviewed wasm), "
            f"{len(keys)} access keys"
        )
        failed |= not same or len(keys) != 0
    with urllib.request.urlopen(
        urllib.request.Request(f"{PAGE}/api/state?d=demo", headers=AGENT), timeout=90
    ) as response:
        state = json.load(response)
        print(f"{PAGE}/api/state?d=demo: HTTP {response.status}, {len(json.dumps(state))} bytes of JSON")
        failed |= response.status != 200 or not state
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
