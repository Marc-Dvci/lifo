# Gas of a settlement

Measured on a local NEAR sandbox (nearcore 2.14.0), not on testnet. Command: `cd e2e && node gas.mjs` after `bash e2e/sandbox.sh start`. Commit `f37070c`. Wasm SHA-256 `3dd9d8fa997cf37487d83b4ea98cda269b509680e8db5479ef192ef739bf252b`. Runtime 659 s.

Each row is a fresh contract on the launch curve with 6 s epochs and 6 s vintages. The accounts buy 1 NEAR each spread over the vintages, then all ask to sell everything in one epoch; one `settle` call settles it; each account then claims. Gas is what the contract's receipts burnt, in Tgas (the limit per call is 300).

| vintages in the settlement | requests | gate bound | settle | claim, mean (max) | request, mean (max) | balance covers reserve, payouts and storage |
|---|---|---|---|---|---|---|
| 1 | 1 | yes | 1.86 | 2.40 (2.40) | 1.94 (1.94) | yes |
| 1 | 48 | yes | 1.87 | 2.43 (2.47) | 1.96 (1.97) | yes |
| 16 | 48 | yes | 2.35 | 2.25 (2.50) | 1.98 (2.00) | yes |
| 48 | 48 | yes | 3.38 | 2.39 (2.65) | 2.01 (2.06) | yes |
