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

Set the required secret key (used for session signing):
```bash
export SECRET_KEY=your-secret-key-here
```

Start the development server:
```bash
uv run uvicorn prague_lions_ranking.main:app --reload
```

Then open http://localhost:8000 in your browser.


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

## Technical Details

**Draw probability (`p_draw`):** Some games allow a draw, some do not. While a standard game of Ultimate does not allow a draw, the ranked matches are often mini games or other variations where a draw can occur. TrueSkill uses a parameter `p_draw` which estimates the probability of a game ending in a draw. If this value is very small, any draw is very significant and holds great value; if it is very large, non-draw results are significant instead. We set `p_draw` by looking at how frequently draws occur across all games — that is the only approach that realistically makes sense.

**Beta parameter:** Beta is a scale of the skill estimates: players whose skills differ by one beta have a 76% probability of the stronger player winning. We use the default value (1), but in the future this could be tuned by inspecting the rankings of players one knows well.

**Gamma parameter:** Gamma is a dynamic factor that addresses the fact that skills change over time and between games. Practically, gamma squared is added to uncertainty between each time tick — for us, that means between each practice. We use the default value (0.03).
