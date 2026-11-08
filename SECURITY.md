# Security

Please report a vulnerability through
[GitHub's private reporting](https://github.com/Marc-Dvci/lifo/security/advisories/new), not in a public issue.
The supported version is the `main` branch, the testnet contracts `launch.lifo.testnet` and `demo.lifo.testnet`,
and the hosted [live page](https://lifo-amt.pages.dev).

LIFO runs on NEAR testnet only. Its tokens and the NEAR in its reserve have no value.

The reports that matter most are anything that:

- pays a holder ahead of a newer vintage when the gate binds, or makes any token's vintage newer than the purchase
  that created it;
- takes NEAR out of the reserve beyond what the curve and the gate allow, or leaves the contract unable to pay its
  reserve, its unclaimed payouts and its storage;
- mints, burns or loses tokens outside a buy from the pool or a settled sale;
- blocks or reorders settlement;
- changes a parameter or the code of a deployed contract (both have no access keys);
- reads the demo accounts' keys from the page's server, or lets one visitor drain the demo accounts past the rate
  limits.
