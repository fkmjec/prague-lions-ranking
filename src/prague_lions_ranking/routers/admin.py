from datetime import timedelta
import json

from fastapi import APIRouter, Depends, HTTPException, status, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from prague_lions_ranking.database import get_db
from prague_lions_ranking.models import User, UserRole, Match, MatchPlayer, Team
from prague_lions_ranking.rating import update_user_ratings
from prague_lions_ranking.auth import (
    authenticate_user,
    create_access_token,
    get_password_hash,
    generate_password,
    require_admin,
    require_login,
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

    access_token = create_access_token(
        data={"sub": user.username},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )

    response = RedirectResponse(url="/leaderboard", status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax",
        secure=True,
    )
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("access_token")
    return response


# Unified views (accessible by all logged-in users, edit controls shown only to admins)

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    users = db.query(User).all()
    is_admin = user.role == UserRole.ADMIN

    # Get flash message from cookie (one-time display) - only relevant for admins
    created_user = request.cookies.get("flash_created_user") if is_admin else None
    created_password = request.cookies.get("flash_created_password") if is_admin else None
    is_reset = request.cookies.get("flash_is_reset") if is_admin else None
    deleted_user = request.cookies.get("flash_deleted_user") if is_admin else None
    ratings_updated = request.cookies.get("flash_ratings_updated") if is_admin else None

    response = templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "is_admin": is_admin,
            "users": users,
            "created_user": created_user,
            "created_password": created_password,
            "is_reset": is_reset,
            "deleted_user": deleted_user,
            "ratings_updated": ratings_updated,
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
    if ratings_updated:
        response.delete_cookie("flash_ratings_updated")

    return response


@router.get("/matches", response_class=HTMLResponse)
async def match_list(
    request: Request,
    user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    is_admin = user.role == UserRole.ADMIN
    if is_admin:
        matches = db.query(Match).order_by(Match.ordering.desc()).all()
        match_deltas = {}
    else:
        matches = (
            db.query(Match)
            .join(MatchPlayer)
            .filter(MatchPlayer.user_id == user.id)
            .order_by(Match.ordering.desc())
            .all()
        )
        # Build match_id → delta lookup from cached rating history
        match_deltas = {}
        if user.rating_history:
            for day in json.loads(user.rating_history):
                for m in day.get("matches", []):
                    match_deltas[m["match_id"]] = m["delta"]

    return templates.TemplateResponse(
        "match_list.html",
        {
            "request": request,
            "user": user,
            "is_admin": is_admin,
            "matches": matches,
            "match_deltas": match_deltas,
        },
    )


@router.get("/matches/{match_id}", response_class=HTMLResponse)
async def match_detail(
    request: Request,
    match_id: int,
    user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    is_admin = user.role == UserRole.ADMIN

    # Collect per-player match stats from cached rating histories
    def _find_match_stats(player: User, mid: int):
        if not player.rating_history:
            return None
        for day in json.loads(player.rating_history):
            for m in day.get("matches", []):
                if m.get("match_id") == mid:
                    return m
        return None

    team_a_ts_before = []
    team_b_ts_before = []
    for player in match.team_a_players:
        stats = _find_match_stats(player, match_id)
        if stats and "ts_after" in stats:
            team_a_ts_before.append(stats["ts_after"] - stats["delta"])
    for player in match.team_b_players:
        stats = _find_match_stats(player, match_id)
        if stats and "ts_after" in stats:
            team_b_ts_before.append(stats["ts_after"] - stats["delta"])

    team_a_avg_before = round(sum(team_a_ts_before) / len(team_a_ts_before), 2) if team_a_ts_before else None
    team_b_avg_before = round(sum(team_b_ts_before) / len(team_b_ts_before), 2) if team_b_ts_before else None
    user_match_stats = _find_match_stats(user, match_id)

    return templates.TemplateResponse(
        "match_detail.html",
        {
            "request": request,
            "user": user,
            "is_admin": is_admin,
            "match": match,
            "team_a_avg_before": team_a_avg_before,
            "team_b_avg_before": team_b_avg_before,
            "user_match_stats": user_match_stats,
        },
    )


# Admin-only actions (POST routes that modify data)

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
            "dashboard.html",
            {
                "request": request,
                "user": admin,
                "is_admin": True,
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
        url="/dashboard",
        status_code=status.HTTP_302_FOUND,
    )
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
        url="/dashboard",
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
        url="/dashboard",
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


@router.get("/admin/matches/new", response_class=HTMLResponse)
async def match_form(
    request: Request,
    insert_before: int = Query(None),
    insert_after: int = Query(None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    # Determine prefill_date from neighbouring matches
    prefill_date = None
    if insert_before:
        before_match = db.query(Match).filter(Match.id == insert_before).first()
        if before_match:
            prefill_date = before_match.date.strftime("%Y-%m-%d")
    elif insert_after:
        after_match = db.query(Match).filter(Match.id == insert_after).first()
        if after_match:
            prefill_date = after_match.date.strftime("%Y-%m-%d")

    if not prefill_date:
        from datetime import date
        prefill_date = date.today().strftime("%Y-%m-%d")

    return templates.TemplateResponse(
        "match_form.html",
        {
            "request": request,
            "user": admin,
            "is_admin": True,
            "prefill_date": prefill_date,
            "insert_before": insert_before,
            "insert_after": insert_after,
        },
    )


@router.post("/admin/matches")
async def create_match(
    request: Request,
    match_date: str = Form(...),
    notes: str = Form(""),
    team_a_players: str = Form(...),
    team_b_players: str = Form(...),
    insert_before: int = Form(None),
    insert_after: int = Form(None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from datetime import date as date_type
    try:
        match_date_parsed = date_type.fromisoformat(match_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    team_a_ids = json.loads(team_a_players) if team_a_players else []
    team_b_ids = json.loads(team_b_players) if team_b_players else []

    if not team_a_ids or not team_b_ids:
        raise HTTPException(status_code=400, detail="Both teams must have at least one player")

    # Create the match with temporary ordering (will be fixed below)
    match = Match(date=match_date_parsed, ordering=0, notes=notes if notes else None)
    db.add(match)
    db.flush()

    for user_id in team_a_ids:
        mp = MatchPlayer(match_id=match.id, user_id=user_id, team=Team.TEAM_A)
        db.add(mp)

    for user_id in team_b_ids:
        mp = MatchPlayer(match_id=match.id, user_id=user_id, team=Team.TEAM_B)
        db.add(mp)

    # Now renumber all matches from 1 to n
    # Build the desired order: get existing matches, insert new one at correct position
    existing_matches = db.query(Match).filter(Match.id != match.id).order_by(Match.ordering.desc()).all()

    # Determine where to insert the new match
    if insert_before and insert_after:
        # Insert between: find position of insert_before and insert after it
        insert_pos = next((i + 1 for i, m in enumerate(existing_matches) if m.id == insert_before), 0)
    elif insert_before:
        # Insert after insert_before (at the end, oldest position)
        insert_pos = next((i + 1 for i, m in enumerate(existing_matches) if m.id == insert_before), len(existing_matches))
    elif insert_after:
        # Insert before insert_after (at the top, newest position)
        insert_pos = next((i for i, m in enumerate(existing_matches) if m.id == insert_after), 0)
    else:
        # No context - insert at position 0 (top/newest)
        insert_pos = 0

    # Build the new ordered list
    ordered_matches = existing_matches[:insert_pos] + [match] + existing_matches[insert_pos:]

    # Assign ordering from n down to 1 (highest = newest)
    for i, m in enumerate(ordered_matches):
        m.ordering = len(ordered_matches) - i

    db.commit()

    return RedirectResponse(url="/matches", status_code=status.HTTP_302_FOUND)


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

    return RedirectResponse(url="/matches", status_code=status.HTTP_302_FOUND)


@router.post("/admin/matches/{match_id}/delete")
async def delete_match(
    match_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    # Explicitly delete MatchPlayer entries first to ensure cleanup
    db.query(MatchPlayer).filter(MatchPlayer.match_id == match_id).delete()
    db.delete(match)
    db.commit()

    return RedirectResponse(url="/matches", status_code=status.HTTP_302_FOUND)


@router.post("/admin/calculate-ratings")
async def calculate_ratings(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    updated_count = update_user_ratings(db)

    response = RedirectResponse(
        url="/dashboard",
        status_code=status.HTTP_302_FOUND,
    )
    response.set_cookie(
        key="flash_ratings_updated",
        value=str(updated_count),
        httponly=True,
        max_age=60,
        samesite="lax",
        secure=True,
    )
    return response


@router.get("/leaderboard", response_class=HTMLResponse)
async def public_leaderboard(
    request: Request,
    user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    players = (
        db.query(User)
        .filter(User.true_skill.isnot(None))
        .filter(User.number_of_practices >= 3)
        .order_by(User.true_skill.desc())
        .limit(10)
        .all()
    )

    is_admin = user.role == UserRole.ADMIN
    rating_history_json = user.rating_history if user.rating_history is not None else "null"

    # Compute teammate sections from cached stats
    teammate_data = json.loads(user.teammate_stats) if user.teammate_stats else []
    most_played = sorted(teammate_data, key=lambda x: -x["games"])[:3]
    eligible = [t for t in teammate_data if t["games"] >= 3]
    best_teammates = sorted(eligible, key=lambda x: -x["win_pct"])[:3]
    worst_teammate = sorted(eligible, key=lambda x: x["win_pct"])[:1]

    return templates.TemplateResponse(
        "leaderboard_public.html",
        {
            "request": request,
            "user": user,
            "is_admin": is_admin,
            "players": players,
            "rating_history_json": rating_history_json,
            "most_played": most_played,
            "best_teammates": best_teammates,
            "worst_teammate": worst_teammate,
        },
    )


@router.get("/admin/leaderboard", response_class=HTMLResponse)
async def private_leaderboard(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    # Private leaderboard: all players with ratings
    players = (
        db.query(User)
        .filter(User.true_skill.isnot(None))
        .order_by(User.true_skill.desc())
        .all()
    )

    return templates.TemplateResponse(
        "leaderboard_private.html",
        {
            "request": request,
            "user": admin,
            "is_admin": True,
            "players": players,
        },
    )


@router.post("/admin/calculate-ratings-leaderboard")
async def calculate_ratings_leaderboard(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    update_user_ratings(db)
    return RedirectResponse(url="/leaderboard", status_code=status.HTTP_302_FOUND)


@router.get("/api/my-rating-history")
async def my_rating_history(
    user: User = Depends(require_login),
):
    history = json.loads(user.rating_history) if user.rating_history else []
    return JSONResponse(content=history)


@router.get("/api/users/search")
async def search_users(
    q: str = Query("", min_length=0),
    user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    if not q:
        users = db.query(User).limit(20).all()
    else:
        users = db.query(User).filter(User.username.ilike(f"%{q}%")).limit(20).all()

    return JSONResponse(
        content=[{"id": u.id, "username": u.username} for u in users]
    )
