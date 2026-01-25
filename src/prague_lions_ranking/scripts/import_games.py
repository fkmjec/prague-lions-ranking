"""Import games from games_db.csv into the database."""

import csv
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from prague_lions_ranking.models import Base, User, Match, MatchPlayer, Team, UserRole


DB_PATH = Path(__file__).parent.parent.parent.parent / "rankings.db"
CSV_PATH = Path(__file__).parent.parent.parent.parent / "games_db.csv"


def parse_team(team_str: str) -> list[str]:
    """Parse team string like '[Player1|Player2|Player3]' into list of player names."""
    # Remove brackets and split by pipe
    team_str = team_str.strip("[]")
    return [name.strip() for name in team_str.split("|") if name.strip()]


def import_games():
    """Import games from CSV file."""
    engine = create_engine(f"sqlite:///{DB_PATH}")
    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        # Clear existing matches and match players
        print("Clearing existing match data...")
        db.query(MatchPlayer).delete()
        db.query(Match).delete()
        db.commit()

        # Collect all unique player names from CSV
        all_players = set()
        games = []

        with open(CSV_PATH, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if not row["date"].strip():
                    continue

                winning_team = parse_team(row["winning_team"])
                losing_team = parse_team(row["losing_team"])

                all_players.update(winning_team)
                all_players.update(losing_team)

                games.append({
                    "date": row["date"],
                    "draw": row["draw"] == "1",
                    "winning_team": winning_team,
                    "losing_team": losing_team,
                })

        print(f"Found {len(all_players)} unique players and {len(games)} games")

        # Create users for players that don't exist
        existing_users = {u.username: u for u in db.query(User).all()}

        for player_name in all_players:
            if player_name not in existing_users:
                user = User(
                    username=player_name,
                    hashed_password="no_login",  # These users can't login
                    role=UserRole.USER,
                )
                db.add(user)
                print(f"  Created user: {player_name}")

        db.commit()

        # Refresh user mapping
        user_map = {u.username: u for u in db.query(User).all()}

        # Import matches
        print("Importing matches...")
        for i, game in enumerate(games):
            match_date = datetime.strptime(game["date"], "%Y-%m-%d").date()

            # For draws: 10-10, for wins: 15-0
            if game["draw"]:
                score_a, score_b = 10, 10
            else:
                score_a, score_b = 15, 0

            match = Match(
                date=match_date,
                ordering=i + 1,
                score_team_a=score_a,
                score_team_b=score_b,
            )
            db.add(match)
            db.flush()

            # Add winning team as Team A
            for player_name in game["winning_team"]:
                user = user_map.get(player_name)
                if user:
                    mp = MatchPlayer(match_id=match.id, user_id=user.id, team=Team.TEAM_A)
                    db.add(mp)

            # Add losing team as Team B
            for player_name in game["losing_team"]:
                user = user_map.get(player_name)
                if user:
                    mp = MatchPlayer(match_id=match.id, user_id=user.id, team=Team.TEAM_B)
                    db.add(mp)

        db.commit()
        print(f"Successfully imported {len(games)} matches!")

    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    import_games()
