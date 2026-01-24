# Prague Lions Ranking

A webapp to keep player rankings for frisbee matches.

## Setup

Install dependencies:
```bash
uv sync
```

Create an admin user:
```bash
uv run create-admin
```

## Running

Start the development server:
```bash
uv run uvicorn prague_lions_ranking.main:app --reload
```

Then open http://localhost:8000 in your browser.

## Database Migration

If upgrading from an older version, run the migration to add rating columns:
```bash
uv run python src/prague_lions_ranking/scripts/add_rating_columns.py
```

## Usage

1. Log in with your admin credentials at `/login`
2. From the dashboard, create new users - passwords are auto-generated
3. Save the generated password when creating users (shown only once)
4. Record matches with scores from the Matches page
5. Click "Calculate Ratings" on the dashboard to compute TrueSkill ratings for all players

## Project Structure

```
src/prague_lions_ranking/
├── main.py          # FastAPI application entry point
├── database.py      # Database configuration
├── models.py        # SQLAlchemy models (User, Match, MatchPlayer)
├── auth.py          # Authentication utilities
├── rating.py        # TrueSkill Through Time rating calculations
├── routers/         # API route handlers
├── scripts/         # CLI scripts (create_admin, add_rating_columns)
├── static/          # Static assets (CSS, JS)
│   ├── styles.css   # Main stylesheet
│   └── scripts.js   # JavaScript functionality
└── templates/       # Jinja2 HTML templates
    └── base.html    # Base template with common structure
```

## Rating System

Player ratings are calculated using the TrueSkill Through Time algorithm, which:
- Considers the full history of matches to compute ratings
- Tracks skill (mu), uncertainty (sigma), and conservative rating (true_skill = mu - 3*sigma)
- Counts unique practice dates and total games per player
