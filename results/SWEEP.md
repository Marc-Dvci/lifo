# The grid: gate G1 and the ranking

Simulated. Command: `python -m lifo.sweep --seeds 10 --workers 4`. Seeds 1 to 10, means over seeds. Commit `500881d`. Curve: 30 NEAR virtual, 1,073,000,000 tokens virtual, max supply 80 %, 16 lots.
Runtime: 491 s for 9000 runs.


Gate G1: 99 of 144 parameter sets give the late group more NEAR back per NEAR under LIFO than under FIFO in both the insider dump and the panic, while calm growth loses at most 0.01 NEAR per NEAR. Ranking: sets whose smaller late-group gain (insider dump, panic) is within 0.02 of the best count as equal; among them, the smallest mean largest one-hour price drop in those two scenarios.


Top of the ranking: GATE_BPS 500, EPOCH_LENGTH 10 min, VINTAGE_LENGTH 1 h, SELL_FEE_BPS 50. The launch set is 500/10/24/50 (passes G1): the 24 h vintage was chosen in run 02 against the refresh attack, see `results/REFRESH.md` and `results/RECOMMENDED.md`. The table below is the top of the ranking; the launch set's rows are marked in the scenario tables.


| scenario | late group LIFO | late group FIFO | gain | worst seed gain | early LIFO | early FIFO | insider LIFO | insider FIFO | 1 h drop LIFO | 1 h drop FIFO |
|---|---|---|---|---|---|---|---|---|---|---|
| calm | 1.284 | 1.275 | 0.009 | -0.034 | 2.220 | 2.308 | 7.012 | 7.124 | 0.234 | 0.223 |
| insider_dump | 0.781 | 0.317 | 0.464 | 0.240 | 1.275 | 1.374 | 1.774 | 5.823 | 0.344 | 0.917 |
| panic | 0.879 | 0.721 | 0.158 | -0.073 | 1.300 | 1.661 | 2.156 | 2.298 | 0.343 | 0.875 |
| slow_distribution | 0.710 | 0.653 | 0.057 | -0.001 | 1.156 | 1.227 | 2.940 | 2.987 | 0.318 | 0.874 |
| refresh_attack | 0.430 | 0.319 | 0.111 | 0.052 | 1.249 | 1.374 | 5.314 | 5.795 | 0.344 | 0.916 |

Refresh attack at these parameters: the insider's refresh cost 0.58 NEAR and raised what it got out by 28.32 NEAR compared with dumping without a refresh.


Top 10 sets passing G1 (gate/epoch/vintage/fee, smaller of the two late-group gains):

- 500/5/1/100: 0.170
- 500/5/6/100: 0.169
- 500/5/24/100: 0.168
- 500/5/1/50: 0.167
- 500/5/6/50: 0.166
- 500/5/24/50: 0.165
- 500/10/1/100: 0.161
- 500/10/6/100: 0.160
- 500/10/24/100: 0.159
- 500/5/1/200: 0.159
