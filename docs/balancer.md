# PATRIC Balancer

The Balancer splits a pool of players into a requested number of teams,
aiming to make every possible matchup between those teams as even as possible.

All code lives in [`balance.py`](../src/prague_lions_ranking/balance.py).

---

## Division objective

A **division** is an assignment of all selected players to N teams.
Its quality is measured by a scalar **objective** — lower means more balanced.

### Per-pair gap

For every distinct pair of teams (i, j) the imbalance is:

```
gap_ij = avg_ts(team i) − avg_ts(team j)

avg_ts(team) = mean of (μ_k − 3·σ_k) over all players k in the team
             = mean conservative (true_skill) rating
```

The gap is in TrueSkill units — the same numbers shown in the UI — so it is
directly interpretable: a gap of 5 means the teams differ on average by
5 TrueSkill points.

### Aggregating over all pairs

With N teams there are C(N, 2) = N(N−1)/2 distinct pairs.
Their gaps are combined into a single objective:

```
objective = Σ  gap_ij²
           i<j
```

Squaring penalises large gaps disproportionately: a gap of 10 contributes 100,
while a gap of 2 contributes only 4.  Two pairs each with a gap of 7 (combined
penalty 98) are preferred over one pair at gap 10 and another at gap 2 (102),
so the optimizer is steered away from any single lopsided matchup.

---

## Algorithms

### `random_swaps` (current default)

A naïve hill-climbing approach:

1. Shuffle all players and assign them round-robin to the N teams (random start).
2. Repeat for **K** iterations:
   a. Pick two teams at random.
   b. Pick one player from each.
   c. Swap them.
   d. If the new objective is strictly lower, keep the swap; otherwise revert.
3. Return the best division found.

**K** defaults to 10 000 and can be set up to 200 000 via the UI.

Because the algorithm only accepts improvements, it performs greedy local search
(hill climbing).  Each run explores a different trajectory from a different
random start, so re-submitting the form may find a better result.

---

## Extensibility

New algorithms can be added without touching the routes or template:

1. Implement a function with the signature:

   ```python
   def my_algo(
       players: list[PlayerRating],
       num_teams: int,
       num_swaps: int,
   ) -> TeamDivision: ...
   ```

2. Register it in the `ALGORITHMS` dict at the bottom of `balance.py`:

   ```python
   ALGORITHMS["my_algo"] = my_algo
   ```

The `algorithm` parameter of `optimize_teams()` selects which one to run.

---

## Future work

- Simulated annealing (accept worse solutions with decreasing probability to escape local optima)
- Multi-start: run *random_swaps* M times from independent seeds, return the global best
- Exhaustive search for small pools (brute-force over all partitions when C(n, n/2) is feasible)
- Constraint support: keep certain players on the same team / on different teams
