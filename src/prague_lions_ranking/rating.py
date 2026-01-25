"""TrueSkill Through Time rating calculation service.

Adapted from the Lions project to work with the webapp's database.
"""

from datetime import datetime
from collections import defaultdict

from sqlalchemy.orm import Session
from trueskillthroughtime import History

from prague_lions_ranking.models import Match, User, Team


# TrueSkill default values (same as Lions project)
MU = 25
SIGMA = 25 / 3
K = 3  # TrueSkill is computed as Mu - K*Sigma
P_DRAW = 1 / 6  # Probability of draw from observed data


def get_matches_with_scores(db: Session) -> list[Match]:
    """Get all matches that have scores recorded, ordered by date and ordering."""
    return (
        db.query(Match)
        .filter(Match.score_team_a.isnot(None), Match.score_team_b.isnot(None))
        .order_by(Match.date, Match.ordering)
        .all()
    )


def match_to_ttt_format(match: Match) -> tuple[list[list[str]], list[int], float]:
    """Convert a match to TrueSkill Through Time format.

    Returns:
        teams: [[team_a_usernames], [team_b_usernames]]
        results: [1, 0] if team_a won, [0, 1] if team_b won, [0, 0] for draw
        time: timestamp in days for TTT
    """
    team_a = [mp.user.username for mp in match.players if mp.team == Team.TEAM_A]
    team_b = [mp.user.username for mp in match.players if mp.team == Team.TEAM_B]

    # Determine winner based on scores
    if match.score_team_a > match.score_team_b:
        # Team A won - put them first
        teams = [team_a, team_b]
        results = [1, 0]
    elif match.score_team_b > match.score_team_a:
        # Team B won - put them first
        teams = [team_b, team_a]
        results = [1, 0]
    else:
        # Draw
        teams = [team_a, team_b]
        results = [0, 0]

    # Convert date to timestamp in days (for TTT time parameter)
    time = datetime.combine(match.date, datetime.min.time()).timestamp() / (60 * 60 * 24)

    return teams, results, time


def calculate_attendance(matches: list[Match]) -> dict[str, dict[str, int]]:
    """Calculate practice attendance, game counts, wins, and draws for all players.

    Returns:
        Dict mapping username to {'practices': int, 'games': int, 'wins': int, 'draws': int}
    """
    player_stats: dict[str, dict[str, set | int]] = defaultdict(
        lambda: {"practice_dates": set(), "games": 0, "wins": 0, "draws": 0}
    )

    for match in matches:
        # Determine winning team
        is_draw = match.score_team_a == match.score_team_b
        team_a_won = match.score_team_a > match.score_team_b
        team_b_won = match.score_team_b > match.score_team_a

        for mp in match.players:
            username = mp.user.username
            player_stats[username]["practice_dates"].add(match.date)
            player_stats[username]["games"] += 1

            if is_draw:
                player_stats[username]["draws"] += 1
            elif (mp.team == Team.TEAM_A and team_a_won) or (mp.team == Team.TEAM_B and team_b_won):
                player_stats[username]["wins"] += 1

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


def update_user_ratings(db: Session) -> int:
    """Calculate and update ratings for all users in the database.

    Returns:
        Number of users updated.
    """
    ratings = calculate_ratings(db)

    if not ratings:
        return 0

    updated_count = 0
    now = datetime.utcnow()

    # Update each user with their rating
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
            updated_count += 1

    # Clear ratings for users who haven't played any matches
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

    db.commit()

    return updated_count
