# Recommended parameters (run 02)

Simulated. Command: `python -m lifo.refresh --seeds 10 --workers 4`. Seeds 1 to 10. Commit `500881d`. Curve: 30 NEAR virtual, 1,073,000,000 tokens virtual, max supply 80 %, 16 lots. Runs: 4000, in `results/refresh.csv`; runtime 195 s.


GATE_BPS 500, EPOCH_LENGTH 10 min, **VINTAGE_LENGTH 24 h**, SELL_FEE_BPS 50, no buy fee, no young-token fee, no minimum age. The gate, epoch and fee are the ones chosen by the 144-set grid of run 01 (`python -m lifo.sweep`, `results/SWEEP.md`), where a 1 h vintage ranked first. The only change in run 02 is the vintage, from 1 h to 24 h; the reasons are in `results/REFRESH.md`. The scenario tables, `results/SENSITIVITY.md` and the figures are drawn at this set.


Late group, NEAR back per NEAR invested, mean of 10 seeds, same agents and seeds under both markets:

| scenario | late group LIFO | late group FIFO | gain | worst seed gain | seeds LIFO better | early LIFO | early FIFO | insider LIFO | insider FIFO | 1 h drop LIFO | 1 h drop FIFO |
|---|---|---|---|---|---|---|---|---|---|---|---|
| calm | 1.284 | 1.275 | +0.009 | -0.034 | 5 of 10 | 2.220 | 2.308 | 7.012 | 7.124 | 0.234 | 0.223 |
| insider_dump | 0.781 | 0.317 | +0.464 | +0.237 | 10 of 10 | 1.271 | 1.374 | 1.811 | 5.823 | 0.344 | 0.917 |
| panic | 0.877 | 0.721 | +0.157 | -0.074 | 7 of 10 | 1.306 | 1.661 | 2.156 | 2.298 | 0.343 | 0.875 |
| slow_distribution | 0.709 | 0.653 | +0.056 | +0.001 | 10 of 10 | 1.165 | 1.227 | 2.940 | 2.987 | 0.318 | 0.874 |
| refresh_attack | 0.449 | 0.319 | +0.130 | +0.084 | 10 of 10 | 1.255 | 1.374 | 5.138 | 5.795 | 0.344 | 0.916 |

Insider dump, the 48 hours after the peak: share of the NEAR paid out that went to the newest third of vintages 74 % (LIFO) against 32 % (FIFO); to the oldest third 23 % against 64 %. Longest wait of the oldest holders for their last fill: 16.0 h (mean over seeds of the longest).


Refresh attack at these parameters: the insider ends with 41.10 NEAR (cash plus holdings) when it refreshes over the 12 hours before the dump, against 25.78 NEAR for its best honest exit and 46.36 NEAR under FIFO. The refresh is not closed. See `results/REFRESH.md`.

