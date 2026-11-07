# End-to-end checks on a local sandbox

Command: `cd e2e && node checks.mjs (local NEAR sandbox 2.14.0, not testnet)`. Commit `a29370680cbb70609e8dc39f0a06d41a018f1847`. Wasm 213191 bytes, SHA-256 `de5cb631a9759d245d3651e30dd5eff4f5d3066a4905d5aa8a35bd62c492675a`.
21 passed, 0 failed.

| check | result | what the chain returned |
|---|---|---|
| new from another account is refused | pass | `{"ActionError":{"index":0,"kind":{"FunctionCallError":{"ExecutionError":"Smart contract panicked: Method new is private"` |
| new from the contract account succeeds once | pass | `ok` |
| a second new is refused | pass | `{"ActionError":{"index":0,"kind":{"FunctionCallError":{"ExecutionError":"Smart contract panicked: The contract has alrea` |
| a buy puts NEAR in the reserve | pass | `tokens 34417247636328908413714067` |
| a deposit on settle is refused | pass | `{"ActionError":{"index":0,"kind":{"FunctionCallError":{"ExecutionError":"Smart contract panicked: Method settle doesn't ` |
| a deposit on claim is refused | pass | `{"ActionError":{"index":0,"kind":{"FunctionCallError":{"ExecutionError":"Smart contract panicked: Method claim doesn't a` |
| a deposit on ft_resolve_transfer is refused | pass | `{"ActionError":{"index":0,"kind":{"FunctionCallError":{"ExecutionError":"Smart contract panicked: Method ft_resolve_tran` |
| a deposit on the view ft_total_supply is accepted and leaves the reserve unchanged | pass | `accepted; reserve 994160000000000000000000 -> 994160000000000000000000` |
| a deposit on the view ft_balance_of is accepted and leaves the reserve unchanged | pass | `accepted; reserve 994160000000000000000000 -> 994160000000000000000000` |
| a deposit on the view ft_metadata is accepted and leaves the reserve unchanged | pass | `accepted; reserve 994160000000000000000000 -> 994160000000000000000000` |
| a deposit on the view storage_balance_bounds is accepted and leaves the reserve unchanged | pass | `accepted; reserve 994160000000000000000000 -> 994160000000000000000000` |
| a deposit on the view storage_balance_of is accepted and leaves the reserve unchanged | pass | `accepted; reserve 994160000000000000000000 -> 994160000000000000000000` |
| a deposit on the view get_params is accepted and leaves the reserve unchanged | pass | `accepted; reserve 994160000000000000000000 -> 994160000000000000000000` |
| a deposit on the view get_request_storage is accepted and leaves the reserve unchanged | pass | `accepted; reserve 994160000000000000000000 -> 994160000000000000000000` |
| a deposit on the view get_pool is accepted and leaves the reserve unchanged | pass | `accepted; reserve 994160000000000000000000 -> 994160000000000000000000` |
| a deposit on the view get_open is accepted and leaves the reserve unchanged | pass | `accepted; reserve 994160000000000000000000 -> 994160000000000000000000` |
| a deposit on the view get_settlement is accepted and leaves the reserve unchanged | pass | `accepted; reserve 994160000000000000000000 -> 994160000000000000000000` |
| a deposit on the view get_account is accepted and leaves the reserve unchanged | pass | `accepted; reserve 994160000000000000000000 -> 994160000000000000000000` |
| a deposit on the view quote_buy is accepted and leaves the reserve unchanged | pass | `accepted; reserve 994160000000000000000000 -> 994160000000000000000000` |
| a deposit on the view contract_source_metadata is accepted and leaves the reserve unchanged | pass | `accepted; reserve 994160000000000000000000 -> 994160000000000000000000` |
| contract_source_metadata has the link and the standards | pass | `{"version":"0.1.0","link":"https://github.com/Marc-Dvci/lifo","standards":[{"standard":"nep141","version":"1.0.0"},{"sta` |
