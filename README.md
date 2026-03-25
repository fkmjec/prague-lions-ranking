# Prague Lions Ranking

A web app for tracking player rankings across frisbee practice sessions. Admins record match results; ratings are computed automatically using TrueSkill Through Time.

## Features

- **Leaderboard** — ranked player list with TrueSkill ratings, games played, and practice attendance
- **Match logging** — mobile-friendly form for recording game results at practice
- **Ratings** — one-click recalculation over the full match history
- **Team Drafter** — balance a selected pool of players into N fair teams using hill-climbing optimisation
- **User management** — admin creates player accounts; passwords are auto-generated and shown once

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python · FastAPI · SQLAlchemy |
| Database | SQLite |
| Templates | Jinja2 |
| Rating engine | TrueSkill Through Time (`trueskillthroughtime`) |
| Package manager | uv |

## Rating System

Player ratings are computed with **TrueSkill Through Time** (TTT; Dangauthier et al., 2007) — a Bayesian skill rating system that extends Microsoft's TrueSkill by running a forward–backward pass over the entire match history. This means early results retroactively sharpen current estimates.

Each player has three values:

| Value | Meaning |
|---|---|
| **μ (mu)** | Skill estimate |
| **σ (sigma)** | Uncertainty around that estimate |
| **TrueSkill** | μ − 3σ — conservative lower bound used for ranking |

Using μ − 3σ means a player needs consistent performance to climb; high uncertainty drags the score down.

### Match Weight

Each match has an integer **weight** (default 1). A match with weight N is entered N times into the TTT computation, amplifying its effect on ratings. It still counts as a single game in all other statistics (game count, teammate stats, attendance). This is intended to give more significance to longer formats (e.g. a full scrimmage) over a single round of minis.

### Key parameters

**`p_draw`** — probability of a draw. Ultimate doesn't allow draws in regulation, but ranked mini-games sometimes end equal. TTT uses this to weight draw outcomes. We set it from the observed draw frequency across all recorded games.

**`β` (beta)** — skill scale. Two players 1β apart have a 76% win probability for the stronger one. We use the library default.

**`γ` (gamma)** — temporal drift. Between each practice, σ² increases by γ², capturing the fact that skills change over time. We use the library default (0.03).

## Setup

```bash
uv sync
uv run create-admin
```

The `create-admin` script prompts for a username and password and writes the admin account to the database.

## Running

```bash
export SECRET_KEY=your-secret-key-here
uv run uvicorn prague_lions_ranking.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000).

## Usage

1. Log in at `/login` with admin credentials
2. Create player accounts from the dashboard — save the generated password (shown once)
3. Record match results from the Matches page
4. Hit **Calculate Ratings** on the dashboard to recompute all TrueSkill scores
5. Use **Team Drafter** to split a player pool into balanced teams before a session

## Docker Deployment

### 1. Create a `.env` file on your server

```bash
echo "SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" > .env
```

### 2. Build and start

```bash
docker compose up -d --build
```

The container binds to `127.0.0.1:8000` only. Use Nginx (or another reverse proxy) to expose it publicly.

### 3. Create the admin user

```bash
docker compose exec -it app uv run create-admin
```

### 4. Nginx reverse proxy example

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Useful commands

```bash
docker compose logs -f        # follow logs
docker compose restart         # restart the container
docker compose down            # stop
docker compose up -d --build   # rebuild after code changes
```

### Database backup

```bash
docker compose cp app:/data/rankings.db ./rankings-backup.db
```

The SQLite database is stored in a Docker named volume (`app-data`) and persists across container rebuilds.

## Project Structure

```
src/prague_lions_ranking/
├── main.py           # FastAPI app and startup
├── models.py         # SQLAlchemy models: User, Match, MatchPlayer
├── database.py       # DB session and engine
├── auth.py           # Session-based authentication helpers
├── rating.py         # TrueSkill Through Time rating calculations
├── balance.py        # Team Drafter balancing algorithms
├── routers/
│   └── admin.py      # All route handlers
├── scripts/
│   ├── create_admin.py
│   └── import_games.py
├── static/
│   └── styles.css
└── templates/        # Jinja2 HTML templates

docs/
└── balancer.md       # Team Drafter algorithm documentation
```
