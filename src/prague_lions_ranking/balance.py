"""Team balancing algorithms for Prague Lions Ranking (PATRIC).

This module provides extensible team balancing functionality.
The main entry point is :func:`optimize_teams`; new algorithms can be
plugged in by registering them in the :data:`ALGORITHMS` dict.

The objective to *minimize* is the sum of squared average-TrueSkill gaps
across all team pairs:

    objective = Σ (avg_ts(team_i) − avg_ts(team_j))²   over all pairs i < j

Squaring penalises very bad matchups much more than small imbalances,
so the algorithm is pushed toward divisions where *every* pair is decent.
avg_ts is the mean conservative rating (μ − K·σ) of a team.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from itertools import combinations
from typing import Callable

# Conservative-rating factor: true_skill = mu - _K * sigma  (matches rating.py)
_K: float = 3.0

# Defaults exported so callers can reference them without magic numbers
DEFAULT_MU: float = 25.0
DEFAULT_SIGMA: float = 25.0 / 3.0
DEFAULT_SWAPS: int = 10_000
MAX_SWAPS: int = 100_000


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class PlayerRating:
    """Lightweight rating snapshot used by the balancing algorithms."""
    username: str
    mu: float
    sigma: float


@dataclass
class TeamDivision:
    """Result of a team-balancing run."""
    teams: list[list[PlayerRating]]
    """Teams; each element is an ordered list of :class:`PlayerRating`."""
    objective: float
    """Objective value = Σ (avg_ts_i − avg_ts_j)² (lower = more balanced)."""
    pair_gaps: dict[str, float]
    """"Team i vs Team j" → absolute average-TrueSkill gap (lower = better)."""


# ---------------------------------------------------------------------------
# Objective helpers
# ---------------------------------------------------------------------------

def _avg_ts(team: list[PlayerRating]) -> float:
    """Average conservative rating (true_skill) of a team."""
    return sum(p.mu - _K * p.sigma for p in team) / len(team)


def division_objective(teams: list[list[PlayerRating]]) -> float:
    """Compute the objective for a team division (lower = more balanced).

    Objective = Σ (avg_ts(team_i) − avg_ts(team_j))² over all distinct pairs.
    """
    avgs = [_avg_ts(t) for t in teams]
    return sum(
        (avgs[i] - avgs[j]) ** 2
        for i, j in combinations(range(len(avgs)), 2)
    )


def _compute_pair_gaps(teams: list[list[PlayerRating]]) -> dict[str, float]:
    avgs = [_avg_ts(t) for t in teams]
    return {
        f"Team {i + 1} vs Team {j + 1}": round(abs(avgs[i] - avgs[j]), 2)
        for i, j in combinations(range(len(avgs)), 2)
    }


# ---------------------------------------------------------------------------
# Initialisation helpers
# ---------------------------------------------------------------------------

def _random_division(players: list[PlayerRating], num_teams: int) -> list[list[PlayerRating]]:
    """Randomly shuffle players and distribute them round-robin across teams."""
    shuffled = players[:]
    random.shuffle(shuffled)
    teams: list[list[PlayerRating]] = [[] for _ in range(num_teams)]
    for i, p in enumerate(shuffled):
        teams[i % num_teams].append(p)
    return teams


# ---------------------------------------------------------------------------
# Algorithm: random swap hill-climbing
# ---------------------------------------------------------------------------

def _random_swaps(
    players: list[PlayerRating],
    num_teams: int,
    num_swaps: int = DEFAULT_SWAPS,
    rng_seed: int | None = None,
) -> TeamDivision:
    """Naïve hill-climbing via random player swaps.

    Algorithm
    ---------
    1. Start from a random division.
    2. Repeat *num_swaps* times:
       a. Pick two players from different (random) teams.
       b. Swap them.
       c. Accept the swap if the objective strictly improves; else revert.
    3. Return the best division found.

    Parameters
    ----------
    players:    Pool of players to divide.
    num_teams:  Number of teams.
    num_swaps:  Maximum swap attempts (capped at :data:`MAX_SWAPS`).
    rng_seed:   Optional seed for reproducibility.
    """
    if rng_seed is not None:
        random.seed(rng_seed)

    teams = _random_division(players, num_teams)
    current_obj = division_objective(teams)

    for _ in range(num_swaps):
        t1, t2 = random.sample(range(num_teams), 2)
        if not teams[t1] or not teams[t2]:
            continue

        p1 = random.randrange(len(teams[t1]))
        p2 = random.randrange(len(teams[t2]))

        # Swap
        teams[t1][p1], teams[t2][p2] = teams[t2][p2], teams[t1][p1]
        new_obj = division_objective(teams)

        if new_obj < current_obj:
            current_obj = new_obj
        else:
            # Revert
            teams[t1][p1], teams[t2][p2] = teams[t2][p2], teams[t1][p1]

    return TeamDivision(
        teams=teams,
        objective=current_obj,
        pair_gaps=_compute_pair_gaps(teams),
    )


# ---------------------------------------------------------------------------
# Algorithm registry
# ---------------------------------------------------------------------------
# To add a new algorithm, implement a function with the signature:
#
#   fn(players: list[PlayerRating], num_teams: int,
#      num_swaps: int, **kwargs) -> TeamDivision
#
# then register it in this dict.

Algorithm = Callable[..., TeamDivision]

ALGORITHMS: dict[str, Algorithm] = {
    "random_swaps": _random_swaps,
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def optimize_teams(
    players: list[PlayerRating],
    num_teams: int,
    num_swaps: int = DEFAULT_SWAPS,
    algorithm: str = "random_swaps",
) -> TeamDivision:
    """Balance *players* into *num_teams* as-equal-as-possible teams.

    Parameters
    ----------
    players:   Players with their current mu/sigma ratings.
    num_teams: Number of teams (must be ≥ 2 and ≤ len(players)).
    num_swaps: Swap budget for iterative algorithms (clipped to [1, MAX_SWAPS]).
    algorithm: Key into :data:`ALGORITHMS` (default ``"random_swaps"``).

    Returns
    -------
    :class:`TeamDivision` describing the best division found.
    """
    if num_teams < 2:
        raise ValueError("Need at least 2 teams.")
    if len(players) < num_teams:
        raise ValueError(f"Cannot form {num_teams} teams from {len(players)} players.")

    num_swaps = min(max(num_swaps, 1), MAX_SWAPS)
    fn = ALGORITHMS.get(algorithm, _random_swaps)
    return fn(players, num_teams, num_swaps)
