import enum
from datetime import datetime

from sqlalchemy import Column, Integer, String, DateTime, Enum, ForeignKey, Text, Date, Float
from sqlalchemy.orm import relationship

from prague_lions_ranking.database import Base


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    USER = "user"


class Team(str, enum.Enum):
    TEAM_A = "team_a"
    TEAM_B = "team_b"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(Enum(UserRole), default=UserRole.USER, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # TrueSkill rating fields
    mu = Column(Float, nullable=True)  # TrueSkill mean rating
    sigma = Column(Float, nullable=True)  # TrueSkill uncertainty/deviation
    true_skill = Column(Float, nullable=True)  # Computed as mu - 3*sigma
    number_of_practices = Column(Integer, nullable=True)  # Unique practice dates
    number_of_games = Column(Integer, nullable=True)  # Total games played
    number_of_wins = Column(Integer, nullable=True)  # Total wins
    number_of_draws = Column(Integer, nullable=True)  # Total draws
    ratings_updated_at = Column(DateTime, nullable=True)  # When ratings were last calculated
    rating_history = Column(Text, nullable=True)  # JSON list of {date, true_skill} points
    teammate_stats = Column(Text, nullable=True)  # JSON list of {teammate, games, wins, win_pct}

    match_participations = relationship("MatchPlayer", back_populates="user")


class Match(Base):
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False)
    ordering = Column(Integer, nullable=False, index=True)
    notes = Column(Text, nullable=True)
    score_team_a = Column(Integer, nullable=True)
    score_team_b = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    players = relationship("MatchPlayer", back_populates="match", cascade="all, delete-orphan")

    @property
    def team_a_players(self):
        return [mp.user for mp in self.players if mp.team == Team.TEAM_A]

    @property
    def team_b_players(self):
        return [mp.user for mp in self.players if mp.team == Team.TEAM_B]

    @property
    def has_score(self):
        return self.score_team_a is not None and self.score_team_b is not None


class MatchPlayer(Base):
    __tablename__ = "match_players"

    id = Column(Integer, primary_key=True, index=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    team = Column(Enum(Team), nullable=False)

    match = relationship("Match", back_populates="players")
    user = relationship("User", back_populates="match_participations")
