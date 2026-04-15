"""TrueSkill Through Time rating calculation service.

Adapted from the Lions project to work with the webapp's database.
"""

import json
from datetime import datetime
from collections import defaultdict

from sqlalchemy.orm import Session
from trueskillthroughtime import History

from prague_lions_ranking.models import Match, User, Team, TEAM_BY_INDEX


# TrueSkill parameters (Xbox Live defaults from the original paper)
MU = 25
SIGMA = 25 / 3
BETA = SIGMA / 2       # performance noise per game
GAMMA = SIGMA / 50     # temporal dynamics (skill drift between time steps)
K = 3                  # TrueSkill is computed as Mu - K*Sigma
P_DRAW = 1 / 6        # Probability of draw from observed data


def get_matches_with_scores(db: Session) -> list[Match]:
    """Get all matches that have scores recorded, ordered by date and ordering."""
    from sqlalchemy import or_
    return (
        db.query(Match)
        .filter(
            or_(
                # Legacy 2-team matches
                Match.score_team_a.isnot(None) & Match.score_team_b.isnot(None),
                # Multiteam matches with scores JSON
                Match.scores.isnot(None),
            )
        )
        .order_by(Match.date, Match.ordering)
        .all()
    )


def scores_to_ttt_results(scores: list[int]) -> list[int]:
    """Convert a list of scores to TTT result format (higher = better).

    Uses dense ranking: teams with the same score get the same result value.
    """
    unique_desc = sorted(set(scores), reverse=True)
    rank_map = {s: i for i, s in enumerate(unique_desc)}
    max_rank = len(unique_desc) - 1
    return [max_rank - rank_map[s] for s in scores]


def match_to_ttt_format(match: Match) -> tuple[list[list[str]], list[int], float]:
    """Convert a match to TrueSkill Through Time format.

    Returns:
        teams: list of team player lists (usernames)
        results: ranking per team (higher = better)
        time: timestamp in days for TTT
    """
    scores = match.parsed_scores

    # Build teams in index order
    team_indices = sorted(set(TEAM_BY_INDEX.index(mp.team) for mp in match.players))
    teams = []
    for idx in team_indices:
        team_enum = TEAM_BY_INDEX[idx]
        teams.append([mp.user.username for mp in match.players if mp.team == team_enum])

    results = scores_to_ttt_results(scores)

    # Convert date to timestamp in days (for TTT time parameter)
    time = datetime.combine(match.date, datetime.min.time()).timestamp() / (60 * 60 * 24)

    return teams, results, time


def _match_team_outcomes(match: Match) -> dict:
    """Compute per-team outcomes for a match.

    Returns dict mapping Team enum → 'win' | 'draw' | 'loss'.
    """
    scores = match.parsed_scores
    team_indices = sorted(set(TEAM_BY_INDEX.index(mp.team) for mp in match.players))

    max_score = max(scores)
    winners = [team_indices[i] for i, s in enumerate(scores) if s == max_score]
    is_draw = len(winners) > 1

    outcomes = {}
    for i, idx in enumerate(team_indices):
        team_enum = TEAM_BY_INDEX[idx]
        if scores[i] == max_score:
            outcomes[team_enum] = "draw" if is_draw else "win"
        else:
            outcomes[team_enum] = "loss"
    return outcomes


def calculate_attendance(matches: list[Match]) -> dict[str, dict[str, int]]:
    """Calculate practice attendance, game counts, wins, and draws for all players.

    Returns:
        Dict mapping username to {'practices': int, 'games': int, 'wins': int, 'draws': int}
    """
    player_stats: dict[str, dict[str, set | int]] = defaultdict(
        lambda: {"practice_dates": set(), "games": 0, "wins": 0, "draws": 0}
    )

    for match in matches:
        outcomes = _match_team_outcomes(match)

        for mp in match.players:
            username = mp.user.username
            player_stats[username]["practice_dates"].add(match.date)
            player_stats[username]["games"] += 1

            outcome = outcomes.get(mp.team, "loss")
            if outcome == "win":
                player_stats[username]["wins"] += 1
            elif outcome == "draw":
                player_stats[username]["draws"] += 1

    # Convert sets to counts
    return {
        username: {
            "practices": len(stats["practice_dates"]),
            "games": stats["games"],
            "wins": stats["wins"],
            "draws": stats["draws"],
        }
        for username, stats in player_stats.items()
    }


def calculate_ratings(db: Session) -> dict[str, dict]:
    """Calculate TrueSkill Through Time ratings for all players.

    Returns:
        Dict mapping username to rating data:
        {
            'mu': float,
            'sigma': float,
            'true_skill': float,
            'practices': int,
            'games': int,
            'wins': int,
            'draws': int,
        }
    """
    matches = get_matches_with_scores(db)

    if not matches:
        return {}

    # Convert matches to TTT format
    compositions = []
    results_list = []
    times = []

    for match in matches:
        teams, results, time = match_to_ttt_format(match)
        weight = getattr(match, "weight", 1) or 1
        for _ in range(weight):
            compositions.append(teams)
            results_list.append(results)
            times.append(time)

    # Create TTT History and compute ratings
    history = History(
        composition=compositions,
        times=times,
        results=results_list,
        p_draw=P_DRAW,
        mu=MU,
        sigma=SIGMA,
        beta=BETA,
        gamma=GAMMA,
    )
    history.convergence()

    # Get all player names from the history
    learning_curves = history.learning_curves()
    player_names = list(learning_curves.keys())

    # Calculate attendance
    attendance = calculate_attendance(matches)

    # Build ratings dictionary
    ratings = {}
    for name in player_names:
        curve = learning_curves[name]
        final_rating = curve[-1][1]  # Get the last Gaussian object
        mu = final_rating.mu
        sigma = final_rating.sigma
        true_skill = mu - K * sigma

        player_attendance = attendance.get(name, {"practices": 0, "games": 0, "wins": 0, "draws": 0})

        ratings[name] = {
            "mu": mu,
            "sigma": sigma,
            "true_skill": true_skill,
            "practices": player_attendance["practices"],
            "games": player_attendance["games"],
            "wins": player_attendance["wins"],
            "draws": player_attendance["draws"],
        }

    return ratings


def calculate_all_rating_histories(
    all_matches: list,
) -> dict[str, list[dict]]:
    """Calculate rating history for every player via prefix computations.

    Runs one TTT pass per match (rather than one per user-match), so all
    players' histories are built in a single shared loop of M runs.

    Returns per-day aggregated data:
        Dict mapping username → [
            {
                'date': 'YYYY-MM-DD',
                'true_skill': float,          # after the last game of that day
                'matches': [
                    {'match_id': int, 'delta': float},  # one entry per game
                    ...
                ]
            },
            ...
        ]
    """
    # Build per-match history first, then aggregate to per-day
    per_match: dict[str, list[dict]] = defaultdict(list)
    initial_ts = round(MU - K * SIGMA, 2)  # starting value before any matches

    compositions: list = []
    results_list: list = []
    times: list = []

    for match in all_matches:
        teams, results, time = match_to_ttt_format(match)
        weight = getattr(match, "weight", 1) or 1
        for _ in range(weight):
            compositions.append(teams)
            results_list.append(results)
            times.append(time)

        h = History(
            composition=compositions,
            times=times,
            results=results_list,
            p_draw=P_DRAW,
            mu=MU,
            sigma=SIGMA,
            beta=BETA,
            gamma=GAMMA,
        )
        h.convergence()

        curves = h.learning_curves()
        date_str = match.date.strftime("%Y-%m-%d")

        for mp in match.players:
            username = mp.user.username
            if username in curves:
                final = curves[username][-1][1]
                ts = round(final.mu - K * final.sigma, 2)
                mu_after = round(final.mu, 4)
                sigma_after = round(final.sigma, 4)

                if per_match[username]:
                    prev = per_match[username][-1]
                    prev_ts = prev["true_skill"]
                    mu_before = prev["mu_after"]
                    sigma_before = prev["sigma_after"]
                else:
                    prev_ts = initial_ts
                    mu_before = MU
                    sigma_before = SIGMA

                per_match[username].append({
                    "date": date_str,
                    "true_skill": ts,
                    "mu_after": mu_after,
                    "sigma_after": sigma_after,
                    "match_id": match.id,
                    "delta": round(ts - prev_ts, 2),
                    "mu_delta": round(mu_after - mu_before, 4),
                    "sigma_delta": round(sigma_after - sigma_before, 4),
                })

    # Aggregate consecutive per-match points that share the same date into one day-point
    result: dict[str, list[dict]] = {}
    for username, points in per_match.items():
        days = []
        i = 0
        while i < len(points):
            date = points[i]["date"]
            j = i + 1
            while j < len(points) and points[j]["date"] == date:
                j += 1
            day_points = points[i:j]
            days.append({
                "date": date,
                "true_skill": day_points[-1]["true_skill"],
                "matches": [
                    {
                        "match_id": p["match_id"],
                        "delta": p["delta"],
                        "mu_delta": p["mu_delta"],
                        "sigma_delta": p["sigma_delta"],
                        "ts_after": p["true_skill"],
                        "mu_after": p["mu_after"],
                        "sigma_after": p["sigma_after"],
                    }
                    for p in day_points
                ],
            })
            i = j
        result[username] = days

    return result


def calculate_teammate_stats(matches: list[Match]) -> dict[str, list[dict]]:
    """Calculate win rate statistics for each player paired with every teammate.

    Returns:
        Dict mapping username → list of {teammate, games, wins, win_pct},
        sorted by games played descending.
    """
    pair: dict[str, dict[str, dict]] = defaultdict(
        lambda: defaultdict(lambda: {"games": 0, "wins": 0})
    )

    for match in matches:
        outcomes = _match_team_outcomes(match)

        # Group players by team
        teams_players: dict[Team, list[str]] = defaultdict(list)
        for mp in match.players:
            teams_players[mp.team].append(mp.user.username)

        for team_enum, players in teams_players.items():
            won = outcomes.get(team_enum) == "win"
            for player in players:
                for teammate in players:
                    if teammate != player:
                        pair[player][teammate]["games"] += 1
                        if won:
                            pair[player][teammate]["wins"] += 1

    result: dict[str, list[dict]] = {}
    for username, teammates in pair.items():
        stats_list = sorted(
            [
                {
                    "teammate": tm,
                    "games": s["games"],
                    "wins": s["wins"],
                    "win_pct": round(s["wins"] / s["games"] * 100, 1),
                }
                for tm, s in teammates.items()
            ],
            key=lambda x: -x["games"],
        )
        result[username] = stats_list

    return result


def update_user_ratings(db: Session) -> int:
    """Calculate and update ratings and rating history for all users.

    Returns:
        Number of users updated.
    """
    matches = get_matches_with_scores(db)
    ratings = calculate_ratings(db)

    if not ratings:
        return 0

    histories = calculate_all_rating_histories(matches)
    teammate_stats = calculate_teammate_stats(matches)

    updated_count = 0
    now = datetime.utcnow()

    for username, rating_data in ratings.items():
        user = db.query(User).filter(User.username == username).first()
        if user:
            user.mu = rating_data["mu"]
            user.sigma = rating_data["sigma"]
            user.true_skill = rating_data["true_skill"]
            user.number_of_practices = rating_data["practices"]
            user.number_of_games = rating_data["games"]
            user.number_of_wins = rating_data["wins"]
            user.number_of_draws = rating_data["draws"]
            user.ratings_updated_at = now
            user.rating_history = json.dumps(histories.get(username, []))
            user.teammate_stats = json.dumps(teammate_stats.get(username, []))
            updated_count += 1

    users_without_matches = (
        db.query(User)
        .filter(User.username.notin_(ratings.keys()))
        .all()
    )
    for user in users_without_matches:
        user.mu = None
        user.sigma = None
        user.true_skill = None
        user.number_of_practices = None
        user.number_of_games = None
        user.number_of_wins = None
        user.number_of_draws = None
        user.ratings_updated_at = now
        user.rating_history = json.dumps([])
        user.teammate_stats = json.dumps([])

    db.commit()

    return updated_count
