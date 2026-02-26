# PATRIC — Prague Lions Ranking: Spec

## Purpose

Track player skill across frisbee practice sessions. Give everyone a fair, transparent ranking they can follow over time.

## User Roles

| Role | Capabilities |
|---|---|
| **Admin** | Create/manage players, record match results, trigger rating recalculation, manage the team |
| **Player** | View leaderboard, view match history, use Team Drafter |

Admins are not ranked and do not appear in the player pool.

## Core Features

### Leaderboard
- Public-facing ranked list of all players
- Shows TrueSkill rating, μ, σ, games played, practices attended
- Sortable; highlights the logged-in player

### Match Logging
- Mobile-friendly form for entering results at practice
- Supports team vs team scores and draws
- Match detail page shows individual player contributions

### Ratings
- Computed on demand via TrueSkill Through Time over the full match history
- Single admin action triggers recalculation for all players

### Team Drafter
- Select a pool of players and a number of teams
- Algorithm: random initial split → hill-climbing via random swaps (accept only improvements)
- Objective: minimise Σ (avg_TrueSkill_i − avg_TrueSkill_j)² across all team pairs
- Configurable swap budget (default 10 000, max 100 000)
- Re-submitting the form tries a different random starting point

### User Management
- Admin creates accounts; passwords are auto-generated and displayed once
- Players log in with username + password
- Session-based authentication

## Tech Stack

- **Backend:** Python, FastAPI, SQLAlchemy
- **Database:** SQLite
- **Templates:** Jinja2
- **Rating engine:** TrueSkill Through Time
- **Package manager:** uv

## Future / Nice-to-Have

- Seasons with per-season leaderboards (plus all-time view)
- Team Drafter: simulated annealing to escape local optima+perhaps try evolution algorithms?
- Add game types to matches and add stats per game type.
