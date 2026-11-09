# Controls: the same gate, rounds and fee, only the order changed

Simulated. Command: `python -m lifo.controls --seeds 10 --workers 4`. Seeds 1 to 10. Commit `dbd00a6`. Runs: 550, in `results/controls.csv`; runtime 31 s.
Launch rules for every capped rule: cap 500 bps of the reserve per round, round 10 min, vintage 24 h, sell fee 50 bps. The FIFO baseline has the same curve and fee. Every capped rule uses the same queue-fear assumption as LIFO (holders panic at 75 % of their usual threshold after a round where the cap was reached).

## 1. Late group, NEAR back per NEAR invested

Mean of 10 seeds, the same agents and seeds in every column.

| scenario | FIFO, at once (main baseline) | cap only, at once | cap + rounds, pro rata | cap + rounds, oldest first | LIFO: cap + rounds, newest first |
|---|---|---|---|---|---|
| calm | 1.275 | 1.284 | 1.283 | 1.280 | 1.284 |
| insider_dump | 0.317 | 0.654 | 0.503 | 0.255 | 0.781 |
| panic | 0.721 | 0.734 | 0.723 | 0.532 | 0.877 |
| slow_distribution | 0.653 | 0.634 | 0.619 | 0.453 | 0.709 |
| refresh_attack | 0.319 | 0.646 | 0.503 | 0.455 | 0.449 |
| **insider's best strategy for each rule** | 0.317 (a) | 0.339 (d) | 0.503 (c1) | 0.255 (a) | 0.449 (c1) |

The last row lets the insider pick, for each rule, the strategy of section 3 that leaves it with the most NEAR (the letter in brackets).

## 2. Where LIFO's gain over the FIFO baseline comes from

Each step adds one thing to the rule before it. Differences of the means above.

| scenario | the cap (cap only minus FIFO) | rounds (cap + rounds, pro rata, minus cap only) | newest first (LIFO minus cap + rounds, pro rata) | LIFO minus FIFO | seeds where LIFO beats cap + rounds, pro rata (of 10) |
|---|---|---|---|---|---|
| calm | +0.008 | -0.001 | +0.001 | +0.009 | 4 |
| insider_dump | +0.337 | -0.151 | +0.278 | +0.464 | 10 |
| panic | +0.014 | -0.011 | +0.154 | +0.157 | 10 |
| slow_distribution | -0.019 | -0.015 | +0.090 | +0.056 | 10 |
| refresh_attack | +0.327 | -0.143 | -0.054 | +0.130 | 2 |
| insider's best strategy for each rule | +0.022 | +0.164 | -0.054 | +0.131 | 2 |

## 3. The insider under each rule

NEAR the insider ends with (8 NEAR put in at launch), cash plus what it still holds at the final price. Mean of 10 seeds. The largest value in each column is the insider's best strategy for that rule.

| insider strategy | FIFO, at once (main baseline) | cap only, at once | cap + rounds, pro rata | cap + rounds, oldest first | LIFO: cap + rounds, newest first |
|---|---|---|---|---|---|
| a. dump at the peak | **46.58** | 15.51 | 28.07 | **43.64** | 14.49 |
| b1. slow exit over 12 h | 19.59 | 30.84 | 17.82 | 28.65 | 16.87 |
| b2. slow exit over 48 h | 24.95 | 25.89 | 25.75 | 25.66 | 25.78 |
| b3. slow exit from the peak, 12 h | 17.98 | 30.85 | 16.31 | 28.59 | 15.37 |
| b4. slow exit from the peak, 48 h | 15.86 | 18.52 | 15.19 | 17.92 | 15.01 |
| c1. refresh over 12 h, then dump | 46.36 | 15.94 | **28.09** | 14.96 | **41.10** |
| c2. refresh 24 to 36 h early, then dump | 46.39 | 15.81 | 27.90 | 17.32 | 30.55 |
| d. dump at the peak, first in every round | 46.58 | **45.41** | 28.07 | 43.64 | 14.49 |

The late group's result under each of those strategies:

| insider strategy | FIFO, at once (main baseline) | cap only, at once | cap + rounds, pro rata | cap + rounds, oldest first | LIFO: cap + rounds, newest first |
|---|---|---|---|---|---|
| a. dump at the peak | 0.317 | 0.654 | 0.503 | 0.255 | 0.781 |
| b1. slow exit over 12 h | 0.708 | 0.546 | 0.646 | 0.386 | 0.783 |
| b2. slow exit over 48 h | 0.761 | 0.731 | 0.708 | 0.652 | 0.751 |
| b3. slow exit from the peak, 12 h | 0.641 | 0.510 | 0.623 | 0.325 | 0.764 |
| b4. slow exit from the peak, 48 h | 0.680 | 0.629 | 0.642 | 0.438 | 0.772 |
| c1. refresh over 12 h, then dump | 0.319 | 0.646 | 0.503 | 0.455 | 0.449 |
| c2. refresh 24 to 36 h early, then dump | 0.319 | 0.662 | 0.505 | 0.415 | 0.602 |
| d. dump at the peak, first in every round | 0.317 | 0.339 | 0.503 | 0.255 | 0.781 |

## 4. Other measures, insider dump at the peak

| measure | FIFO, at once (main baseline) | cap only, at once | cap + rounds, pro rata | cap + rounds, oldest first | LIFO: cap + rounds, newest first |
|---|---|---|---|---|---|
| early buyers, NEAR back per NEAR | 1.374 | 1.383 | 1.406 | 1.574 | 1.271 |
| largest one-hour price drop | 92 % | 34 % | 34 % | 34 % | 34 % |
| share of the crash payouts to the newest third of vintages | 32 % | 66 % | 52 % | 30 % | 74 % |
| oldest holders' mean wait for their last fill (hours) | 0.0 | 2.5 | 4.7 | 2.1 | 4.7 |
| rounds where the cap was reached | 0 | 226 | 225 | 225 | 225 |
