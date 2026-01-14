from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from prague_lions_ranking.database import engine, Base
from prague_lions_ranking.routers import admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Prague Lions Ranking", lifespan=lifespan)

app.include_router(admin.router)


@app.get("/")
async def root():
    return RedirectResponse(url="/login")
