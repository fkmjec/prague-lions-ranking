from datetime import timedelta

from datetime import datetime
import json

from fastapi import APIRouter, Depends, HTTPException, status, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from prague_lions_ranking.database import get_db
from prague_lions_ranking.models import User, UserRole, Match, MatchPlayer, Team
from prague_lions_ranking.auth import (
    authenticate_user,
    create_access_token,
    get_password_hash,
    generate_password,
    require_admin,
    ACCESS_TOKEN_EXPIRE_MINUTES,
)

from pathlib import Path

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = authenticate_user(db, username, password)
    if not user:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid username or password"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    if user.role != UserRole.ADMIN:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Admin access required"},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    access_token = create_access_token(
        data={"sub": user.username},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )

    response = RedirectResponse(url="/admin/dashboard", status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax",
        secure=True,
    )
    return response


@router.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    users = db.query(User).all()

    # Get flash message from cookie (one-time display)
    created_user = request.cookies.get("flash_created_user")
    created_password = request.cookies.get("flash_created_password")
    is_reset = request.cookies.get("flash_is_reset")
    deleted_user = request.cookies.get("flash_deleted_user")

    response = templates.TemplateResponse(
        "admin_dashboard.html",
        {
            "request": request,
            "admin": admin,
            "users": users,
            "created_user": created_user,
            "created_password": created_password,
            "is_reset": is_reset,
            "deleted_user": deleted_user,
        },
    )

    # Clear flash cookies after reading
    if created_user:
        response.delete_cookie("flash_created_user")
    if created_password:
        response.delete_cookie("flash_created_password")
    if is_reset:
        response.delete_cookie("flash_is_reset")
    if deleted_user:
        response.delete_cookie("flash_deleted_user")

    return response


@router.post("/admin/users")
async def create_user(
    request: Request,
    username: str = Form(...),
    role: str = Form("user"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    existing_user = db.query(User).filter(User.username == username).first()
    if existing_user:
        users = db.query(User).all()
        return templates.TemplateResponse(
            "admin_dashboard.html",
            {
                "request": request,
                "admin": admin,
                "users": users,
                "error": f"User '{username}' already exists",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    password = generate_password()
    user_role = UserRole.ADMIN if role == "admin" else UserRole.USER

    new_user = User(
        username=username,
        hashed_password=get_password_hash(password),
        role=user_role,
    )
    db.add(new_user)
    db.commit()

    response = RedirectResponse(
        url="/admin/dashboard",
        status_code=status.HTTP_302_FOUND,
    )
    # Use secure httponly cookies for flash messages (not logged in browser history)
    response.set_cookie(
        key="flash_created_user",
        value=username,
        httponly=True,
        max_age=60,
        samesite="lax",
        secure=True,
    )
    response.set_cookie(
        key="flash_created_password",
        value=password,
        httponly=True,
        max_age=60,
        samesite="lax",
        secure=True,
    )
    return response


@router.post("/admin/users/{user_id}/reset-password")
async def reset_user_password(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    new_password = generate_password()
    user.hashed_password = get_password_hash(new_password)
    db.commit()

    response = RedirectResponse(
        url="/admin/dashboard",
        status_code=status.HTTP_302_FOUND,
    )
    response.set_cookie(
        key="flash_created_user",
        value=user.username,
        httponly=True,
        max_age=60,
        samesite="lax",
        secure=True,
    )
    response.set_cookie(
        key="flash_created_password",
        value=new_password,
        httponly=True,
        max_age=60,
        samesite="lax",
        secure=True,
    )
    response.set_cookie(
        key="flash_is_reset",
        value="true",
        httponly=True,
        max_age=60,
        samesite="lax",
        secure=True,
    )
    return response


@router.post("/admin/users/{user_id}/delete")
async def delete_user(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Prevent self-deletion
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    username = user.username
    db.delete(user)
    db.commit()

    response = RedirectResponse(
        url="/admin/dashboard",
        status_code=status.HTTP_302_FOUND,
    )
    response.set_cookie(
        key="flash_deleted_user",
        value=username,
        httponly=True,
        max_age=60,
        samesite="lax",
        secure=True,
    )
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("access_token")
    return response


# Match management routes

@router.get("/admin/matches", response_class=HTMLResponse)
async def match_list(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    matches = db.query(Match).order_by(Match.date.desc()).all()
    return templates.TemplateResponse(
        "match_list.html",
        {"request": request, "admin": admin, "matches": matches},
    )


@router.get("/admin/matches/new", response_class=HTMLResponse)
async def match_form(
    request: Request,
    prefill_date: str = Query(None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(
        "match_form.html",
        {"request": request, "admin": admin, "prefill_date": prefill_date},
    )


@router.post("/admin/matches")
async def create_match(
    request: Request,
    match_date: str = Form(...),
    notes: str = Form(""),
    team_a_players: str = Form(...),
    team_b_players: str = Form(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        date = datetime.fromisoformat(match_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    team_a_ids = json.loads(team_a_players) if team_a_players else []
    team_b_ids = json.loads(team_b_players) if team_b_players else []

    if not team_a_ids or not team_b_ids:
        raise HTTPException(status_code=400, detail="Both teams must have at least one player")

    match = Match(date=date, notes=notes if notes else None)
    db.add(match)
    db.flush()

    for user_id in team_a_ids:
        mp = MatchPlayer(match_id=match.id, user_id=user_id, team=Team.TEAM_A)
        db.add(mp)

    for user_id in team_b_ids:
        mp = MatchPlayer(match_id=match.id, user_id=user_id, team=Team.TEAM_B)
        db.add(mp)

    db.commit()

    return RedirectResponse(url="/admin/matches", status_code=status.HTTP_302_FOUND)


@router.get("/admin/matches/{match_id}", response_class=HTMLResponse)
async def match_detail(
    request: Request,
    match_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    return templates.TemplateResponse(
        "match_detail.html",
        {"request": request, "admin": admin, "match": match},
    )


@router.post("/admin/matches/{match_id}/score")
async def update_match_score(
    match_id: int,
    score_team_a: int = Form(...),
    score_team_b: int = Form(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    match.score_team_a = score_team_a
    match.score_team_b = score_team_b
    db.commit()

    return RedirectResponse(url="/admin/matches", status_code=status.HTTP_302_FOUND)


@router.post("/admin/matches/{match_id}/delete")
async def delete_match(
    match_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    db.delete(match)
    db.commit()

    return RedirectResponse(url="/admin/matches", status_code=status.HTTP_302_FOUND)


@router.get("/api/users/search")
async def search_users(
    q: str = Query("", min_length=0),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not q:
        users = db.query(User).limit(20).all()
    else:
        users = db.query(User).filter(User.username.ilike(f"%{q}%")).limit(20).all()

    return JSONResponse(
        content=[{"id": u.id, "username": u.username} for u in users]
    )
