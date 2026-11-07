# Gas of a settlement

Measured on a local NEAR sandbox (nearcore 2.14.0), not on testnet. Command: `cd e2e && node gas.mjs` after `bash e2e/sandbox.sh start`. Commit `6d56955`. Wasm SHA-256 `de5cb631a9759d245d3651e30dd5eff4f5d3066a4905d5aa8a35bd62c492675a`. Runtime 658 s.

Each row is a fresh contract on the launch curve with 6 s epochs and 6 s vintages. The accounts buy 1 NEAR each spread over the vintages, then all ask to sell everything in one epoch; one `settle` call settles it; each account then claims. Gas is what the contract's receipts burnt, in Tgas (the limit per call is 300).

| vintages in the settlement | requests | gate bound | settle | claim, mean (max) | request, mean (max) | balance covers reserve, payouts and storage |
|---|---|---|---|---|---|---|
| 1 | 1 | yes | 1.86 | 2.40 (2.40) | 1.94 (1.94) | yes |
| 1 | 48 | yes | 1.87 | 2.43 (2.46) | 1.96 (1.97) | yes |
| 16 | 48 | yes | 2.36 | 2.26 (2.52) | 1.99 (2.00) | yes |
| 48 | 48 | yes | 3.41 | 2.43 (2.69) | 2.02 (2.08) | yes |
