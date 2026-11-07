# Sensitivity to the queue-fear assumption

Simulated, not market data. Command: `python -m lifo.sweep --seeds 10 --workers 4`. Seeds 1 to 10, means over seeds. Commit `f92a59b`. Curve: 30 NEAR virtual, 1,073,000,000 tokens virtual, max supply 80 %, 16 lots.
Runtime: 513 s for 9000 runs.


LIFO at 500/10/1/50. A holder normally sells everything when the price falls a drawn fraction below its peak since entry; after a batch where the gate bound, that fraction is multiplied by the queue fear (1.0 = the visible queue changes nothing, 0.5 = holders panic at half the usual fall). Late group, NEAR back per NEAR, mean over seeds:

| queue fear | calm | insider dump | panic |
|---|---|---|---|
| 0.5 | 1.263 | 0.812 | 0.891 |
| 0.75 | 1.284 | 0.781 | 0.879 |
| 1.0 | 1.276 | 0.751 | 0.862 |
