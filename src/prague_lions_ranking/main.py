from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from prague_lions_ranking.database import engine, Base
from prague_lions_ranking.routers import admin

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    # Add columns introduced after initial schema creation (silently skip if already present)
    with engine.connect() as conn:
        for stmt in [
            "ALTER TABLE users ADD COLUMN rating_history TEXT",
            "ALTER TABLE users ADD COLUMN teammate_stats TEXT",
            "ALTER TABLE matches ADD COLUMN weight INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE matches ADD COLUMN is_multiteam INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE matches ADD COLUMN scores TEXT",
        ]:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                pass
    yield


app = FastAPI(title="Prague Lions Ranking", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(admin.router)


@app.exception_handler(StarletteHTTPException)
async def auth_redirect_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code in (401, 403) and not request.url.path.startswith("/api/"):
        return RedirectResponse(url="/login", status_code=302)
    return await http_exception_handler(request, exc)


@app.get("/")
async def root():
    return RedirectResponse(url="/login")
