# The refresh attack: decomposition and countermeasures

Simulated. Command: `python -m lifo.refresh --seeds 10 --workers 4`. Seeds 1 to 10. Commit `500881d`. Curve: 30 NEAR virtual, 1,073,000,000 tokens virtual, max supply 80 %, 16 lots. Runs: 4000, in `results/refresh.csv`; runtime 195 s.

﻿
## Findings

All numbers are from the tables below (simulated, 10 seeds).

**1. The refresh is a profitable attack, not griefing.** At the run 01 rules the insider's best honest exit is a 48 hour slow exit, worth 25.79 NEAR. The refresh over 12 hours followed by a dump is worth 42.51 NEAR, 16.72 more, and it is better for the insider on 10 of 10 seeds than the 12 hour slow exit, the 48 hour slow exit and the plain dump. It also does the most damage: the late group gets 0.430 NEAR per NEAR against 0.750 to 0.783 when the insider exits honestly. The refresh turns the insider's exit back into roughly what FIFO gives it (46.36 NEAR for the same refresh under FIFO, 46.58 for a plain dump). A plain dump under LIFO is the strategy LIFO punishes (14.19 NEAR). Slow exits are not punished at all: a 48 hour slow exit gets 25.79 under LIFO and 24.95 under FIFO, because sales below the gate are filled in full.

A refresh done 24 to 36 hours before the dump (c2) is worth 31.52 NEAR. It is worse for the insider than a refresh in the last 12 hours (c1) on 10 of 10 seeds, probably because the late group keeps buying after it and those later buyers are ahead of it in the line. So the attack pays most when the refresh is recent, and it still pays 5.7 NEAR more than the best honest exit when it is not.

**2. No fee or lock closes it.** A fee can only close it if it costs about as much as the priority is worth, and the priority is worth 28.3 NEAR here (42.51 for the refresh against 14.19 for the plain dump). Measured on the insider's refresh (c1), against 42.51 NEAR with no countermeasure: a buy fee of 50 bps (M1) gives 42.33; a young-token sell fee of 200 bps for 6 h (M2) gives 42.32, for 24 h 42.07, and of 500 bps for 24 h 41.17; a minimum age of 1 h (M4) gives 42.63 and of 6 h gives 43.61, which is higher. The insider keeps the late refresh under M2 (42.07 against 31.78 for the early one). What they cost genuine buyers: M1 doubles the round-trip cost to 0.99 %; M2 at 200 bps charges a buyer who sells within 24 h 2.49 % instead of 0.50 %, which falls on exactly the panicking late buyers LIFO is meant to protect; M4 locks a buyer's tokens for the minimum age.

**3. A longer vintage helps only by accident of timing.** With a 72 hour or 7 day vintage the refresh gain falls to +10.76 and +4.63 NEAR when the peak is at day 20, presumably because the refresh then lands in the same vintage as most of the late group and is filled pro rata with it. That result depends on where the insider chooses to dump: with the peak at day 22, just after a vintage boundary, the same variants give +16.41 and +16.43, as bad as one hour vintages (+17.31). The insider knows the boundaries and picks the time. A 7 day vintage also costs the late group 0.05 NEAR per NEAR in the insider dump (0.731 against 0.781) and 0.06 in the panic (0.822 against 0.879).

**4. Recommendation: a 24 hour vintage and nothing else.** It is the one change that costs genuine buyers nothing (round trip stays 0.50 %), leaves every other result where it was (insider dump 0.781 in both, panic 0.877 against 0.879, calm 1.284 in both), and reduces the refresh gain by about one NEAR in both timings tested (+15.32 against +16.72 at peak day 20, +16.38 against +17.31 at day 22; the late group in the refresh moves from 0.430 to 0.449, better on 10 of 10 seeds). It also stretches the 16-lot limit from 16 hours of purchases to 16 days, which matters more for ordinary holders than for the attack. The buy fee, the young-token fee and the minimum age stay in the model as options set to 0; the launch rules use none of them.

**5. What remains possible.** An insider who plans the dump can still refresh in the last hours before it and exit roughly as under FIFO: 41.10 NEAR for 8 NEAR put in, against 25.78 for its best honest exit. LIFO still beats FIFO for the late group in that case, 0.449 against 0.319, on 10 of 10 seeds, so the attack removes most of LIFO's advantage against this insider and does not make the late group worse off than FIFO in this simulation. The attack needs the insider's tokens to be sellable in calm epochs and costs two fees, 0.5 % each at the launch rules. Every rule tested here depends only on the age of tokens and on fees, never on who owns an account, because the insider can open any number of accounts.

**6. An idea not tested.** Priority for new money only: a vintage's tokens would be given priority up to the net NEAR that entered the pool in that vintage (buys minus sells), and the rest would be treated as the oldest vintage. A refresh adds no net money, so it would gain no priority. It was not built in this run. It needs a new gate rule and a lot of bookkeeping per vintage, and it opens a different hole: selling in the same vintage in which the late group buys would cost them their priority. It is a candidate for a later run, not a claim.


## 1. Decomposition at the run 01 rules (v1 (run 01 rules))

Gate 500, epoch 10 min, vintage 1 h, sell fee 50. The insider put in 8 NEAR at launch. Means over 10 seeds, same agents and seeds for every row. "Cash out" is the NEAR the insider received; "total" adds what it still holds, valued at the final price net of the sell fee. In strategies b1, b2 and c the insider sells in slices of equal size from 12 h (or 48 h) before the peak until it holds nothing (b) or, in c, buys back with each payout from a second account and dumps everything at the peak.

| insider strategy | insider LIFO: cash out | insider LIFO: total | insider FIFO: total | late group LIFO | late group FIFO | seeds where LIFO is better for the late group (of 10) |
|---|---|---|---|---|---|---|
| a. dump at the peak | 14.19 | 14.19 | 46.58 | 0.781 | 0.317 | 10 |
| b1. slow exit over 12 h | 16.85 | 16.85 | 19.59 | 0.783 | 0.708 | 10 |
| b2. slow exit over 48 h | 25.79 | 25.79 | 24.95 | 0.750 | 0.761 | 5 |
| b3. slow exit from the peak, 12 h | 15.36 | 15.36 | 17.98 | 0.764 | 0.641 | 10 |
| b4. slow exit from the peak, 48 h | 15.00 | 15.00 | 15.86 | 0.773 | 0.680 | 9 |
| c1. refresh over 12 h, then dump | 42.51 | 42.51 | 46.36 | 0.430 | 0.319 | 10 |
| c2. refresh 24 to 36 h early, then dump | 31.52 | 31.52 | 46.39 | 0.590 | 0.319 | 10 |

The same at the recommended design (M3 vintage 24 h):

| insider strategy | insider LIFO: cash out | insider LIFO: total | insider FIFO: total | late group LIFO | late group FIFO | seeds where LIFO is better for the late group (of 10) |
|---|---|---|---|---|---|---|
| a. dump at the peak | 14.49 | 14.49 | 46.58 | 0.781 | 0.317 | 10 |
| b1. slow exit over 12 h | 16.87 | 16.87 | 19.59 | 0.783 | 0.708 | 10 |
| b2. slow exit over 48 h | 25.78 | 25.78 | 24.95 | 0.751 | 0.761 | 5 |
| b3. slow exit from the peak, 12 h | 15.37 | 15.37 | 17.98 | 0.764 | 0.641 | 10 |
| b4. slow exit from the peak, 48 h | 15.01 | 15.01 | 15.86 | 0.772 | 0.680 | 9 |
| c1. refresh over 12 h, then dump | 41.10 | 41.10 | 46.36 | 0.449 | 0.319 | 10 |
| c2. refresh 24 to 36 h early, then dump | 30.55 | 30.55 | 46.39 | 0.602 | 0.319 | 10 |

Paired per seed, LIFO, at the run 01 rules:

- refresh vs slow exit over 12 h: the insider ends with more NEAR in the refresh on 10 of 10 seeds; the late group does worse in the refresh on 10 of 10 seeds.
- refresh vs slow exit over 48 h: the insider ends with more NEAR in the refresh on 10 of 10 seeds; the late group does worse in the refresh on 10 of 10 seeds.
- refresh vs plain dump: the insider ends with more NEAR in the refresh on 10 of 10 seeds; the late group does worse in the refresh on 10 of 10 seeds.
- early refresh (c2) vs slow exit over 48 h: the insider ends with more NEAR in the refresh on 8 of 10 seeds; the late group does worse in the refresh on 7 of 10 seeds.
- early refresh (c2) vs late refresh (c1): the insider ends with more NEAR in the refresh on 0 of 10 seeds; the late group does worse in the refresh on 0 of 10 seeds.

## 2. Countermeasures (10 seeds, LIFO unless said otherwise; FIFO has the same fees)

Insider: total NEAR (cash plus holdings) under each strategy, the best one, and the refresh minus the best honest strategy (a, b1 to b4); c1 and c2 are the two refreshes. Positive means the refresh pays more than any honest exit.

| countermeasure | a | b1 | b2 | b3 | b4 | c1 | c2 | best strategy | best refresh minus best honest |
|---|---|---|---|---|---|---|---|---|---|
| v1 (run 01 rules) | 14.19 | 16.85 | 25.79 | 15.36 | 15.00 | 42.51 | 31.52 | c1 | +16.72 |
| M1 buy fee 50 | 14.29 | 16.91 | 25.75 | 15.41 | 15.00 | 42.33 | 31.49 | c1 | +16.58 |
| M2 young 6 h, +200 | 14.21 | 16.89 | 25.80 | 15.37 | 15.01 | 42.32 | 31.55 | c1 | +16.52 |
| M2 young 24 h, +200 | 14.28 | 16.94 | 25.82 | 15.38 | 15.03 | 42.07 | 31.78 | c1 | +16.25 |
| M2 young 24 h, +500 | 14.38 | 17.05 | 25.86 | 15.46 | 15.11 | 41.17 | 31.98 | c1 | +15.31 |
| M3 vintage 24 h | 14.49 | 16.87 | 25.78 | 15.37 | 15.01 | 41.10 | 30.55 | c1 | +15.32 |
| M3 vintage 72 h | 14.54 | 16.87 | 25.78 | 15.37 | 15.01 | 36.53 | 36.44 | c1 | +10.76 |
| M3 vintage 7 d | 14.73 | 16.87 | 25.76 | 15.38 | 15.01 | 30.39 | 30.26 | c1 | +4.63 |
| M4 min age 1 h | 14.23 | 16.85 | 25.79 | 15.41 | 15.00 | 42.63 | 31.58 | c1 | +16.84 |
| M4 min age 6 h | 14.33 | 17.42 | 25.72 | 15.47 | 15.04 | 43.61 | 32.47 | c1 | +17.89 |
| M1 + M2 (24 h, +200) | 14.36 | 16.98 | 25.78 | 15.45 | 15.07 | 41.74 | 31.62 | c1 | +15.96 |
| M2 (24 h, +200) + M3 24 h | 14.60 | 16.99 | 25.82 | 15.42 | 15.05 | 40.72 | 30.83 | c1 | +14.90 |
| M3 72 h + M1 | 14.64 | 16.93 | 25.74 | 15.42 | 15.00 | 36.42 | 36.34 | c1 | +10.69 |
| M3 72 h + M2 (6 h, +200) | 14.73 | 17.01 | 25.85 | 15.47 | 15.11 | 36.26 | 36.17 | c1 | +10.42 |
| M3 7 d + M1 | 14.83 | 16.94 | 25.72 | 15.43 | 15.01 | 30.33 | 30.21 | c1 | +4.61 |
| M1 + M2 (24 h, +200) + M3 24 h | 14.68 | 17.07 | 25.79 | 15.49 | 15.10 | 40.42 | 30.69 | c1 | +14.63 |
| v1 (run 01 rules) [peak day 22] | 14.65 | 16.52 | 25.66 | 15.86 | 15.29 | 42.97 | 32.77 | c1 | +17.31 |
| M3 vintage 24 h [peak day 22] | 14.79 | 16.52 | 25.65 | 15.86 | 15.29 | 42.04 | 31.60 | c1 | +16.38 |
| M3 vintage 72 h [peak day 22] | 14.79 | 16.52 | 25.65 | 15.86 | 15.29 | 42.07 | 26.36 | c1 | +16.41 |
| M3 vintage 7 d [peak day 22] | 14.92 | 16.52 | 25.64 | 15.87 | 15.29 | 42.07 | 25.13 | c1 | +16.43 |

Late group, NEAR back per NEAR invested, LIFO (FIFO) and the number of seeds out of 10 where LIFO is better:

| countermeasure | insider dump | panic | calm growth | refresh c1 | refresh c2 | insider's best strategy: late group |
|---|---|---|---|---|---|---|
| v1 (run 01 rules) | 0.781 (0.317), 10/10 | 0.879 (0.721), 7/10 | 1.284 (1.275), 5/10 | 0.430 (0.319), 10/10 | 0.590 (0.319), 10/10 | 0.430 (0.319) |
| M1 buy fee 50 | 0.782 (0.319), 10/10 | 0.879 (0.717), 7/10 | 1.278 (1.272), 5/10 | 0.433 (0.321), 10/10 | 0.591 (0.321), 10/10 | 0.433 (0.321) |
| M2 young 6 h, +200 | 0.781 (0.317), 10/10 | 0.878 (0.720), 7/10 | 1.284 (1.275), 5/10 | 0.430 (0.320), 10/10 | 0.589 (0.319), 10/10 | 0.430 (0.320) |
| M2 young 24 h, +200 | 0.781 (0.316), 10/10 | 0.879 (0.720), 7/10 | 1.285 (1.275), 5/10 | 0.432 (0.322), 10/10 | 0.587 (0.318), 10/10 | 0.432 (0.322) |
| M2 young 24 h, +500 | 0.776 (0.314), 10/10 | 0.877 (0.717), 7/10 | 1.285 (1.275), 5/10 | 0.434 (0.323), 10/10 | 0.582 (0.316), 10/10 | 0.434 (0.323) |
| M3 vintage 24 h | 0.781 (0.317), 10/10 | 0.877 (0.721), 7/10 | 1.284 (1.275), 5/10 | 0.449 (0.319), 10/10 | 0.602 (0.319), 10/10 | 0.449 (0.319) |
| M3 vintage 72 h | 0.769 (0.317), 10/10 | 0.865 (0.721), 7/10 | 1.284 (1.275), 5/10 | 0.512 (0.319), 10/10 | 0.513 (0.319), 10/10 | 0.512 (0.319) |
| M3 vintage 7 d | 0.731 (0.317), 10/10 | 0.822 (0.721), 7/10 | 1.284 (1.275), 5/10 | 0.556 (0.319), 10/10 | 0.558 (0.319), 10/10 | 0.556 (0.319) |
| M4 min age 1 h | 0.781 (0.315), 10/10 | 0.878 (0.719), 7/10 | 1.284 (1.275), 5/10 | 0.428 (0.316), 10/10 | 0.589 (0.316), 10/10 | 0.428 (0.316) |
| M4 min age 6 h | 0.777 (0.309), 10/10 | 0.875 (0.717), 7/10 | 1.283 (1.275), 6/10 | 0.412 (0.362), 9/10 | 0.574 (0.311), 10/10 | 0.412 (0.362) |
| M1 + M2 (24 h, +200) | 0.779 (0.318), 10/10 | 0.878 (0.720), 7/10 | 1.279 (1.273), 5/10 | 0.434 (0.321), 10/10 | 0.588 (0.320), 10/10 | 0.434 (0.321) |
| M2 (24 h, +200) + M3 24 h | 0.780 (0.316), 10/10 | 0.877 (0.716), 8/10 | 1.285 (1.275), 5/10 | 0.451 (0.322), 10/10 | 0.600 (0.318), 10/10 | 0.451 (0.322) |
| M3 72 h + M1 | 0.769 (0.319), 10/10 | 0.866 (0.717), 8/10 | 1.278 (1.272), 5/10 | 0.514 (0.321), 10/10 | 0.516 (0.321), 10/10 | 0.514 (0.321) |
| M3 72 h + M2 (6 h, +200) | 0.764 (0.315), 10/10 | 0.861 (0.716), 7/10 | 1.284 (1.274), 5/10 | 0.510 (0.320), 10/10 | 0.512 (0.320), 10/10 | 0.510 (0.320) |
| M3 7 d + M1 | 0.732 (0.319), 10/10 | 0.822 (0.717), 7/10 | 1.278 (1.272), 5/10 | 0.557 (0.321), 10/10 | 0.560 (0.321), 10/10 | 0.557 (0.321) |
| M1 + M2 (24 h, +200) + M3 24 h | 0.779 (0.318), 10/10 | 0.876 (0.719), 7/10 | 1.279 (1.273), 5/10 | 0.453 (0.321), 10/10 | 0.600 (0.320), 10/10 | 0.453 (0.321) |
| v1 (run 01 rules) [peak day 22] | 0.790 (0.338), 10/10 | 0.811 (0.718), 7/10 | 1.290 (1.291), 5/10 | 0.420 (0.339), 9/10 | 0.576 (0.339), 9/10 | 0.420 (0.339) |
| M3 vintage 24 h [peak day 22] | 0.788 (0.338), 10/10 | 0.810 (0.718), 7/10 | 1.291 (1.291), 5/10 | 0.436 (0.339), 9/10 | 0.593 (0.339), 9/10 | 0.436 (0.339) |
| M3 vintage 72 h [peak day 22] | 0.767 (0.338), 10/10 | 0.792 (0.718), 6/10 | 1.290 (1.291), 5/10 | 0.427 (0.339), 9/10 | 0.648 (0.339), 10/10 | 0.427 (0.339) |
| M3 vintage 7 d [peak day 22] | 0.727 (0.338), 10/10 | 0.754 (0.718), 5/10 | 1.290 (1.291), 5/10 | 0.411 (0.339), 9/10 | 0.623 (0.339), 9/10 | 0.411 (0.339) |

What a genuine buyer pays: share of 1 NEAR lost in a round trip in a calm market (buy at the start of a vintage, sell after the time shown; every fee counted). "locked" means the tokens cannot be requested yet.

| countermeasure | sold after 1 h | sold after 6 h | sold after 24 h | sold after 72 h |
|---|---|---|---|---|
| v1 (run 01 rules) | 0.50 % | 0.50 % | 0.50 % | 0.50 % |
| M1 buy fee 50 | 0.99 % | 0.99 % | 0.99 % | 0.99 % |
| M2 young 6 h, +200 | 2.49 % | 2.49 % | 0.50 % | 0.50 % |
| M2 young 24 h, +200 | 2.49 % | 2.49 % | 2.49 % | 0.50 % |
| M2 young 24 h, +500 | 5.47 % | 5.47 % | 5.47 % | 0.50 % |
| M3 vintage 24 h | 0.50 % | 0.50 % | 0.50 % | 0.50 % |
| M3 vintage 72 h | 0.50 % | 0.50 % | 0.50 % | 0.50 % |
| M3 vintage 7 d | 0.50 % | 0.50 % | 0.50 % | 0.50 % |
| M4 min age 1 h | locked | 0.50 % | 0.50 % | 0.50 % |
| M4 min age 6 h | locked | locked | 0.50 % | 0.50 % |
| M1 + M2 (24 h, +200) | 2.97 % | 2.97 % | 2.97 % | 0.99 % |
| M2 (24 h, +200) + M3 24 h | 2.49 % | 2.49 % | 2.49 % | 0.50 % |
| M3 72 h + M1 | 0.99 % | 0.99 % | 0.99 % | 0.99 % |
| M3 72 h + M2 (6 h, +200) | 2.49 % | 2.49 % | 2.49 % | 0.50 % |
| M3 7 d + M1 | 0.99 % | 0.99 % | 0.99 % | 0.99 % |
| M1 + M2 (24 h, +200) + M3 24 h | 2.97 % | 2.97 % | 2.97 % | 0.99 % |
| v1 (run 01 rules) [peak day 22] | 0.50 % | 0.50 % | 0.50 % | 0.50 % |
| M3 vintage 24 h [peak day 22] | 0.50 % | 0.50 % | 0.50 % | 0.50 % |
| M3 vintage 72 h [peak day 22] | 0.50 % | 0.50 % | 0.50 % | 0.50 % |
| M3 vintage 7 d [peak day 22] | 0.50 % | 0.50 % | 0.50 % | 0.50 % |

Largest one-hour price drop and early buyers' result in the insider dump, LIFO:

| countermeasure | 1 h drop | early buyers per NEAR |
|---|---|---|
| v1 (run 01 rules) | 0.34 | 1.275 |
| M1 buy fee 50 | 0.34 | 1.277 |
| M2 young 6 h, +200 | 0.34 | 1.275 |
| M2 young 24 h, +200 | 0.34 | 1.282 |
| M2 young 24 h, +500 | 0.34 | 1.284 |
| M3 vintage 24 h | 0.34 | 1.271 |
| M3 vintage 72 h | 0.34 | 1.292 |
| M3 vintage 7 d | 0.34 | 1.312 |
| M4 min age 1 h | 0.34 | 1.275 |
| M4 min age 6 h | 0.34 | 1.286 |
| M1 + M2 (24 h, +200) | 0.34 | 1.278 |
| M2 (24 h, +200) + M3 24 h | 0.34 | 1.277 |
| M3 72 h + M1 | 0.34 | 1.294 |
| M3 72 h + M2 (6 h, +200) | 0.34 | 1.296 |
| M3 7 d + M1 | 0.34 | 1.315 |
| M1 + M2 (24 h, +200) + M3 24 h | 0.34 | 1.274 |
| v1 (run 01 rules) [peak day 22] | 0.34 | 1.226 |
| M3 vintage 24 h [peak day 22] | 0.34 | 1.227 |
| M3 vintage 72 h [peak day 22] | 0.34 | 1.250 |
| M3 vintage 7 d [peak day 22] | 0.34 | 1.271 |
