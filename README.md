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

## Usage

1. Log in with your admin credentials at `/login`
2. From the dashboard, create new users - passwords are auto-generated
3. Save the generated password when creating users (shown only once)

## Project Structure

```
src/prague_lions_ranking/
├── main.py          # FastAPI application entry point
├── database.py      # Database configuration
├── models.py        # SQLAlchemy models
├── auth.py          # Authentication utilities
├── routers/         # API route handlers
├── scripts/         # CLI scripts
├── static/          # Static assets (CSS, JS)
│   ├── styles.css   # Main stylesheet
│   └── scripts.js   # JavaScript functionality
└── templates/       # Jinja2 HTML templates
    └── base.html    # Base template with common structure
```
