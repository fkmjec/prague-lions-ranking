from datetime import timedelta
import json

from fastapi import APIRouter, Depends, HTTPException, status, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from prague_lions_ranking.database import get_db
from prague_lions_ranking.models import User, UserRole, Match, MatchPlayer, Team, TEAM_BY_INDEX, Pod, PodPlayer
from prague_lions_ranking.rating import update_user_ratings
from prague_lions_ranking.balance import (
    optimize_teams,
    PlayerRating,
    DEFAULT_MU,
    DEFAULT_SIGMA,
    DEFAULT_SWAPS,
)
from prague_lions_ranking.auth import (
    authenticate_user,
    create_access_token,
    get_password_hash,
    generate_password,
    verify_password,
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

    # Build per-team avg TrueSkill before this match
    num_teams = match.num_teams
    team_avg_before = []
    for idx in range(num_teams):
        ts_before = []
        for player in match.get_team_players(idx):
            stats = _find_match_stats(player, match_id)
            if stats and "ts_after" in stats:
                ts_before.append(stats["ts_after"] - stats["delta"])
        team_avg_before.append(round(sum(ts_before) / len(ts_before), 2) if ts_before else None)

    user_match_stats = _find_match_stats(user, match_id)

    # For backward compat with 2-team template
    team_a_avg_before = team_avg_before[0] if len(team_avg_before) > 0 else None
    team_b_avg_before = team_avg_before[1] if len(team_avg_before) > 1 else None

    # Build teams data for template
    teams_data = []
    scores = match.parsed_scores
    for idx in range(num_teams):
        teams_data.append({
            "index": idx,
            "label": f"Team {idx + 1}" if match.is_multiteam else ("Team A" if idx == 0 else "Team B"),
            "players": match.get_team_players(idx),
            "score": scores[idx] if scores and idx < len(scores) else None,
            "avg_before": team_avg_before[idx] if idx < len(team_avg_before) else None,
        })

    return templates.TemplateResponse(
        "match_detail.html",
        {
            "request": request,
            "user": user,
            "is_admin": is_admin,
            "match": match,
            "team_a_avg_before": team_a_avg_before,
            "team_b_avg_before": team_b_avg_before,
            "teams_data": teams_data,
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
    def _user_error(msg: str):
        users = db.query(User).all()
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "user": admin,
                "is_admin": True,
                "users": users,
                "error": msg,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if not username.isascii():
        return _user_error(f"Username '{username}' contains non-ASCII characters. Please use only letters A–Z, digits, and standard symbols.")

    existing_user = db.query(User).filter(User.username == username).first()
    if existing_user:
        return _user_error(f"User '{username}' already exists.")

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


@router.get("/users/change-password", response_class=HTMLResponse)
async def change_password_page(
    request: Request,
    user: User = Depends(require_login),
):
    return templates.TemplateResponse(
        "change_password.html",
        {"request": request, "is_admin": user.role == UserRole.ADMIN},
    )


@router.post("/users/change-password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    is_admin = user.role == UserRole.ADMIN

    def error(msg):
        return templates.TemplateResponse(
            "change_password.html",
            {"request": request, "is_admin": is_admin, "error": msg},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if not verify_password(current_password, user.hashed_password):
        return error("Current password is incorrect.")
    if new_password != confirm_password:
        return error("New passwords do not match.")
    if len(new_password) < 8:
        return error("New password must be at least 8 characters.")

    user.hashed_password = get_password_hash(new_password)
    db.commit()

    return templates.TemplateResponse(
        "change_password.html",
        {"request": request, "is_admin": is_admin, "success": True},
    )


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

    pods = db.query(Pod).order_by(Pod.name).all()
    pods_json = json.dumps([
        {
            "id": pod.id,
            "name": pod.name,
            "players": [{"id": pp.user.id, "username": pp.user.username} for pp in pod.players],
        }
        for pod in pods
    ])

    return templates.TemplateResponse(
        "match_form.html",
        {
            "request": request,
            "user": admin,
            "is_admin": True,
            "prefill_date": prefill_date,
            "insert_before": insert_before,
            "insert_after": insert_after,
            "pods_json": pods_json,
        },
    )


STANDARD_MATCH_TYPES = {"Mini", "Redzone", "Scrimmage"}


@router.post("/admin/matches")
async def create_match(
    request: Request,
    match_date: str = Form(...),
    match_type: str = Form("Mini"),
    match_type_other: str = Form(""),
    weight: int = Form(1),
    notes: str = Form(""),
    team_a_players: str = Form("[]"),
    team_b_players: str = Form("[]"),
    is_multiteam: int = Form(0),
    teams_json: str = Form("[]"),
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

    final_type = match_type_other.strip() if match_type == "Other" else match_type
    if not final_type:
        final_type = "Other"

    # Clamp weight to 1–3
    weight = max(1, min(3, weight))

    # Parse teams: multiteam sends teams_json, standard sends team_a/team_b
    if is_multiteam:
        all_teams = json.loads(teams_json) if teams_json else []
        if len(all_teams) < 2:
            raise HTTPException(status_code=400, detail="At least 2 teams required")
        if len(all_teams) > 5:
            raise HTTPException(status_code=400, detail="Maximum 5 teams allowed")
        if any(len(t) == 0 for t in all_teams):
            raise HTTPException(status_code=400, detail="Each team must have at least one player")
    else:
        team_a_ids = json.loads(team_a_players) if team_a_players else []
        team_b_ids = json.loads(team_b_players) if team_b_players else []
        if not team_a_ids or not team_b_ids:
            raise HTTPException(status_code=400, detail="Both teams must have at least one player")
        all_teams = [team_a_ids, team_b_ids]

    # Create the match with temporary ordering (will be fixed below)
    match = Match(
        date=match_date_parsed, ordering=0, match_type=final_type,
        notes=notes if notes else None, weight=weight,
        is_multiteam=1 if is_multiteam else 0,
    )
    db.add(match)
    db.flush()

    for team_idx, team_ids in enumerate(all_teams):
        team_enum = TEAM_BY_INDEX[team_idx]
        for user_id in team_ids:
            mp = MatchPlayer(match_id=match.id, user_id=user_id, team=team_enum)
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
    request: Request,
    match_id: int,
    score_team_a: int = Form(None),
    score_team_b: int = Form(None),
    scores_json: str = Form(None),
    weight: int = Form(None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    if match.is_multiteam and scores_json:
        scores = json.loads(scores_json)
        match.scores = json.dumps(scores)
    elif score_team_a is not None and score_team_b is not None:
        match.score_team_a = score_team_a
        match.score_team_b = score_team_b

    if weight is not None:
        match.weight = max(1, min(3, weight))
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


@router.get("/admin/matches/{match_id}/edit", response_class=HTMLResponse)
async def edit_match_form(
    request: Request,
    match_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    pods = db.query(Pod).order_by(Pod.name).all()
    pods_json = json.dumps([
        {
            "id": pod.id,
            "name": pod.name,
            "players": [{"id": pp.user.id, "username": pp.user.username} for pp in pod.players],
        }
        for pod in pods
    ])

    # Build current teams as JSON for JS pre-population
    num_teams = match.num_teams
    current_teams = []
    for idx in range(num_teams):
        team_players = match.get_team_players(idx)
        current_teams.append([{"id": p.id, "username": p.username} for p in team_players])
    current_teams_json = json.dumps(current_teams)

    # Determine current match type for the select
    current_type = match.match_type or ""
    is_standard_type = current_type in STANDARD_MATCH_TYPES
    match_type_select = current_type if is_standard_type else "Other"
    match_type_other_val = current_type if not is_standard_type else ""

    return templates.TemplateResponse(
        "match_edit.html",
        {
            "request": request,
            "user": admin,
            "is_admin": True,
            "match": match,
            "pods_json": pods_json,
            "current_teams_json": current_teams_json,
            "match_type_select": match_type_select,
            "match_type_other_val": match_type_other_val,
        },
    )


@router.post("/admin/matches/{match_id}/edit")
async def edit_match(
    request: Request,
    match_id: int,
    match_date: str = Form(...),
    match_type: str = Form("Mini"),
    match_type_other: str = Form(""),
    weight: int = Form(1),
    notes: str = Form(""),
    team_a_players: str = Form("[]"),
    team_b_players: str = Form("[]"),
    is_multiteam: int = Form(0),
    teams_json: str = Form("[]"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from datetime import date as date_type

    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    try:
        match_date_parsed = date_type.fromisoformat(match_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    final_type = match_type_other.strip() if match_type == "Other" else match_type
    if not final_type:
        final_type = "Other"

    weight = max(1, min(3, weight))

    if is_multiteam:
        all_teams = json.loads(teams_json) if teams_json else []
        if len(all_teams) < 2:
            raise HTTPException(status_code=400, detail="At least 2 teams required")
        if len(all_teams) > 5:
            raise HTTPException(status_code=400, detail="Maximum 5 teams allowed")
        if any(len(t) == 0 for t in all_teams):
            raise HTTPException(status_code=400, detail="Each team must have at least one player")
    else:
        team_a_ids = json.loads(team_a_players) if team_a_players else []
        team_b_ids = json.loads(team_b_players) if team_b_players else []
        if not team_a_ids or not team_b_ids:
            raise HTTPException(status_code=400, detail="Both teams must have at least one player")
        all_teams = [team_a_ids, team_b_ids]

    # Update match metadata
    match.date = match_date_parsed
    match.match_type = final_type
    match.weight = weight
    match.notes = notes if notes else None
    match.is_multiteam = 1 if is_multiteam else 0

    # Clear old scores if team structure changed (2-team ↔ multiteam)
    if is_multiteam:
        match.score_team_a = None
        match.score_team_b = None
    else:
        match.scores = None

    # Replace all players: delete old, insert new
    db.query(MatchPlayer).filter(MatchPlayer.match_id == match.id).delete()
    db.flush()

    for team_idx, team_ids in enumerate(all_teams):
        team_enum = TEAM_BY_INDEX[team_idx]
        for user_id in team_ids:
            db.add(MatchPlayer(match_id=match.id, user_id=user_id, team=team_enum))

    db.commit()

    return RedirectResponse(
        url=f"/matches/{match.id}",
        status_code=status.HTTP_302_FOUND,
    )


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
    from sqlalchemy import or_
    players = (
        db.query(User)
        .filter(User.true_skill.isnot(None))
        .filter(or_(User.number_of_practices >= 2, User.number_of_games >= 3))
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

    # Compute personal stats summary
    player_stats = None
    if user.number_of_games is not None:
        games = user.number_of_games or 0
        wins = user.number_of_wins or 0
        draws = user.number_of_draws or 0
        peak_ts = None
        peak_date = None
        if user.rating_history:
            history = json.loads(user.rating_history)
            if history:
                peak_entry = max(history, key=lambda x: x["true_skill"])
                peak_ts = round(peak_entry["true_skill"], 2)
                peak_date = peak_entry["date"]
        player_stats = {
            "games": games,
            "practices": user.number_of_practices or 0,
            "wins": wins,
            "draws": draws,
            "losses": games - wins - draws,
            "peak_ts": peak_ts,
            "peak_date": peak_date,
        }

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
            "player_stats": player_stats,
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


@router.get("/about", response_class=HTMLResponse)
async def about(
    request: Request,
    user: User = Depends(require_login),
):
    return templates.TemplateResponse(
        "about.html",
        {
            "request": request,
            "user": user,
            "is_admin": user.role == UserRole.ADMIN,
        },
    )


@router.get("/seasons", response_class=HTMLResponse)
async def seasons(
    request: Request,
    user: User = Depends(require_login),
):
    return templates.TemplateResponse(
        "seasons.html",
        {
            "request": request,
            "user": user,
            "is_admin": user.role == UserRole.ADMIN,
        },
    )


@router.get("/balance", response_class=HTMLResponse)
async def balance_page(
    request: Request,
    user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    all_players = db.query(User).filter(User.role == UserRole.USER).order_by(User.username).all()
    return templates.TemplateResponse(
        "balance.html",
        {
            "request": request,
            "user": user,
            "is_admin": user.role == UserRole.ADMIN,
            "all_players": all_players,
            "selected": [],
            "form_num_teams": 2,
            "form_num_swaps": DEFAULT_SWAPS,
            "result": None,
            "error": None,
        },
    )


@router.post("/balance", response_class=HTMLResponse)
async def balance_compute(
    request: Request,
    user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    form = await request.form()
    selected_usernames: list[str] = form.getlist("players")
    try:
        num_teams = int(form.get("num_teams", 2))
    except (TypeError, ValueError):
        num_teams = 2
    try:
        num_swaps = int(form.get("num_swaps", DEFAULT_SWAPS))
    except (TypeError, ValueError):
        num_swaps = DEFAULT_SWAPS

    all_players = db.query(User).filter(User.role == UserRole.USER).order_by(User.username).all()
    is_admin = user.role == UserRole.ADMIN

    def _error(msg: str):
        return templates.TemplateResponse(
            "balance.html",
            {
                "request": request,
                "user": user,
                "is_admin": is_admin,
                "all_players": all_players,
                "selected": selected_usernames,
                "form_num_teams": num_teams,
                "form_num_swaps": num_swaps,
                "result": None,
                "error": msg,
            },
            status_code=400,
        )

    if len(selected_usernames) < 2:
        return _error("Select at least 2 players.")
    if len(selected_usernames) < num_teams:
        return _error(f"Cannot form {num_teams} teams from {len(selected_usernames)} players.")

    username_map = {u.username: u for u in all_players}
    player_ratings = [
        PlayerRating(
            username=name,
            mu=username_map[name].mu if (name in username_map and username_map[name].mu is not None) else DEFAULT_MU,
            sigma=username_map[name].sigma if (name in username_map and username_map[name].sigma is not None) else DEFAULT_SIGMA,
        )
        for name in selected_usernames
        if name in username_map
    ]

    try:
        division = optimize_teams(player_ratings, num_teams, num_swaps)
    except ValueError as exc:
        return _error(str(exc))

    def _fmt(v: float) -> str:
        return f"{v:.1f}"

    teams_data = [
        {
            "players": [
                {"username": p.username, "true_skill": _fmt(p.mu - 3 * p.sigma)}
                for p in sorted(team, key=lambda p: -(p.mu - 3 * p.sigma))
            ],
            "avg_skill": _fmt(
                sum(p.mu - 3 * p.sigma for p in team) / len(team)
            ) if team else "–",
        }
        for team in division.teams
    ]
    result = {
        "objective": division.objective,
        "pair_gaps": division.pair_gaps,
        "teams": teams_data,
        "teams_usernames_json": json.dumps(
            [[p["username"] for p in team["players"]] for team in teams_data]
        ),
    }

    return templates.TemplateResponse(
        "balance.html",
        {
            "request": request,
            "user": user,
            "is_admin": is_admin,
            "all_players": all_players,
            "selected": selected_usernames,
            "form_num_teams": num_teams,
            "form_num_swaps": num_swaps,
            "result": result,
            "error": None,
        },
    )


# ── Pods ──────────────────────────────────────────────────────────────────────

def _pods_list(db: Session):
    """Return serialised pod data for templates."""
    pods = db.query(Pod).order_by(Pod.name).all()
    result = []
    for pod in pods:
        skills = [pp.user.true_skill for pp in pod.players if pp.user.true_skill is not None]
        avg_skill = round(sum(skills) / len(skills), 1) if skills else None
        result.append({
            "id": pod.id,
            "name": pod.name,
            "is_temp": pod.name.startswith("temp_"),
            "players": [{"id": pp.user.id, "username": pp.user.username} for pp in pod.players],
            "avg_skill": avg_skill,
        })
    return result


@router.get("/admin/pods", response_class=HTMLResponse)
async def pods_page(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    all_players = db.query(User).filter(User.role == UserRole.USER).order_by(User.username).all()
    return templates.TemplateResponse(
        "pods.html",
        {
            "request": request,
            "user": admin,
            "is_admin": True,
            "pods": _pods_list(db),
            "all_players": all_players,
        },
    )


@router.post("/admin/pods")
async def create_pod(
    pod_name: str = Form(...),
    player_ids: str = Form("[]"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    name = pod_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Pod name cannot be empty")
    if db.query(Pod).filter(Pod.name == name).first():
        raise HTTPException(status_code=400, detail="A pod with this name already exists")

    ids = json.loads(player_ids) if player_ids else []
    pod = Pod(name=name)
    db.add(pod)
    db.flush()
    for user_id in ids:
        db.add(PodPlayer(pod_id=pod.id, user_id=user_id))
    db.commit()
    return RedirectResponse(url="/admin/pods", status_code=status.HTTP_302_FOUND)


@router.post("/admin/pods/delete-temp")
async def delete_temp_pods(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    for pod in db.query(Pod).filter(Pod.name.like("temp_%")).all():
        db.delete(pod)
    db.commit()
    return RedirectResponse(url="/admin/pods", status_code=status.HTTP_302_FOUND)


@router.post("/admin/pods/save-temp")
async def save_temp_pods(
    teams_json: str = Form(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from datetime import date
    today = date.today().isoformat()
    teams = json.loads(teams_json)
    username_to_id = {u.username: u.id for u in db.query(User).filter(User.role == UserRole.USER).all()}

    for i, usernames in enumerate(teams, 1):
        base = f"temp_{today}_pod_{i}"
        name = base
        counter = 2
        while db.query(Pod).filter(Pod.name == name).first():
            name = f"{base}_{counter}"
            counter += 1
        pod = Pod(name=name)
        db.add(pod)
        db.flush()
        for username in usernames:
            if username in username_to_id:
                db.add(PodPlayer(pod_id=pod.id, user_id=username_to_id[username]))
    db.commit()
    return RedirectResponse(url="/admin/pods", status_code=status.HTTP_302_FOUND)


@router.post("/admin/pods/{pod_id}/rename")
async def rename_pod(
    pod_id: int,
    new_name: str = Form(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    pod = db.query(Pod).filter(Pod.id == pod_id).first()
    if not pod:
        raise HTTPException(status_code=404, detail="Pod not found")
    name = new_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Pod name cannot be empty")
    if db.query(Pod).filter(Pod.name == name, Pod.id != pod_id).first():
        raise HTTPException(status_code=400, detail="A pod with this name already exists")
    pod.name = name
    db.commit()
    return RedirectResponse(url="/admin/pods", status_code=status.HTTP_302_FOUND)


@router.post("/admin/pods/{pod_id}/delete")
async def delete_pod(
    pod_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    pod = db.query(Pod).filter(Pod.id == pod_id).first()
    if not pod:
        raise HTTPException(status_code=404, detail="Pod not found")
    db.delete(pod)
    db.commit()
    return RedirectResponse(url="/admin/pods", status_code=status.HTTP_302_FOUND)


@router.get("/admin/pods/{pod_id}/edit", response_class=HTMLResponse)
async def edit_pod_form(
    request: Request,
    pod_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    pod = db.query(Pod).filter(Pod.id == pod_id).first()
    if not pod:
        raise HTTPException(status_code=404, detail="Pod not found")

    all_players = db.query(User).filter(User.role == UserRole.USER).order_by(User.username).all()
    current_player_ids = {pp.user_id for pp in pod.players}

    return templates.TemplateResponse(
        "pod_edit.html",
        {
            "request": request,
            "user": admin,
            "is_admin": True,
            "pod": pod,
            "all_players": all_players,
            "current_player_ids": current_player_ids,
        },
    )


@router.post("/admin/pods/{pod_id}/edit")
async def edit_pod(
    pod_id: int,
    pod_name: str = Form(...),
    player_ids: str = Form("[]"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    pod = db.query(Pod).filter(Pod.id == pod_id).first()
    if not pod:
        raise HTTPException(status_code=404, detail="Pod not found")

    name = pod_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Pod name cannot be empty")
    if db.query(Pod).filter(Pod.name == name, Pod.id != pod_id).first():
        raise HTTPException(status_code=400, detail="A pod with this name already exists")

    ids = json.loads(player_ids) if player_ids else []

    pod.name = name
    # Replace pod roster: delete old PodPlayer rows, insert new ones.
    # This does not touch MatchPlayer rows, so past matches are unaffected.
    db.query(PodPlayer).filter(PodPlayer.pod_id == pod_id).delete()
    for user_id in ids:
        db.add(PodPlayer(pod_id=pod_id, user_id=user_id))

    db.commit()
    return RedirectResponse(url="/admin/pods", status_code=status.HTTP_302_FOUND)


@router.get("/api/my-rating-history")
async def my_rating_history(
    user: User = Depends(require_login),
):
    history = json.loads(user.rating_history) if user.rating_history else []
    return JSONResponse(content=history)


@router.get("/admin/diagnostics", response_class=PlainTextResponse)
async def diagnostics(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Plain-text admin diagnostics for data integrity checks."""
    from collections import defaultdict
    from sqlalchemy import or_, func

    lines: list[str] = []

    def hdr(title: str) -> None:
        lines.append("")
        lines.append("=" * 70)
        lines.append(title)
        lines.append("=" * 70)

    # 1. Duplicate (match_id, user_id) pairs ---------------------------------
    hdr("1. Duplicate MatchPlayer rows (same user in same match twice)")
    dup_rows = (
        db.query(
            MatchPlayer.match_id,
            MatchPlayer.user_id,
            func.count(MatchPlayer.id).label("n"),
        )
        .group_by(MatchPlayer.match_id, MatchPlayer.user_id)
        .having(func.count(MatchPlayer.id) > 1)
        .all()
    )
    if not dup_rows:
        lines.append("  OK -- no duplicates.")
    else:
        lines.append(f"  FOUND {len(dup_rows)} duplicate pair(s):")
        for match_id, user_id, n in dup_rows:
            u = db.query(User).filter(User.id == user_id).first()
            m = db.query(Match).filter(Match.id == match_id).first()
            uname = u.username if u else f"?user_id={user_id}"
            mdate = m.date if m else "?"
            rows = db.query(MatchPlayer).filter(
                MatchPlayer.match_id == match_id,
                MatchPlayer.user_id == user_id,
            ).all()
            teams = ", ".join(mp.team.value if mp.team else "?" for mp in rows)
            lines.append(
                f"  match_id={match_id} date={mdate} user={uname} rows={n} teams=[{teams}]"
            )

    # 2. Orphaned MatchPlayer rows -------------------------------------------
    hdr("2. Orphaned MatchPlayer rows (no matching Match)")
    orphans = (
        db.query(MatchPlayer)
        .outerjoin(Match, Match.id == MatchPlayer.match_id)
        .filter(Match.id.is_(None))
        .all()
    )
    if not orphans:
        lines.append("  OK -- no orphans.")
    else:
        lines.append(f"  FOUND {len(orphans)} orphan(s):")
        for mp in orphans:
            lines.append(f"  match_player.id={mp.id} match_id={mp.match_id} user_id={mp.user_id}")

    # 3. Matches without players (probably ok, but worth knowing) -----------
    hdr("3. Matches with zero MatchPlayer rows")
    empty_matches = (
        db.query(Match)
        .outerjoin(MatchPlayer, MatchPlayer.match_id == Match.id)
        .group_by(Match.id)
        .having(func.count(MatchPlayer.id) == 0)
        .all()
    )
    if not empty_matches:
        lines.append("  OK -- every match has players.")
    else:
        lines.append(f"  FOUND {len(empty_matches)} empty match(es):")
        for m in empty_matches:
            lines.append(f"  match_id={m.id} date={m.date}")

    # 4. Per-player games and practices --------------------------------------
    hdr("4. Per-player games/practices (scored matches only)")
    scored_matches = (
        db.query(Match)
        .filter(
            or_(
                Match.score_team_a.isnot(None) & Match.score_team_b.isnot(None),
                Match.scores.isnot(None),
            )
        )
        .order_by(Match.date, Match.ordering)
        .all()
    )

    per_player: dict[str, dict] = defaultdict(
        lambda: {"dates": set(), "games": 0, "per_date": defaultdict(int)}
    )
    for m in scored_matches:
        for mp in m.players:
            name = mp.user.username
            per_player[name]["dates"].add(m.date)
            per_player[name]["games"] += 1
            per_player[name]["per_date"][m.date] += 1

    lines.append(f"  {'Player':<20} {'games':>6} {'practices':>10}  games/date")
    lines.append("  " + "-" * 68)
    for name in sorted(per_player.keys(), key=str.lower):
        s = per_player[name]
        per_date_summary = ", ".join(
            f"{d}:{n}" for d, n in sorted(s["per_date"].items())
        )
        lines.append(
            f"  {name:<20} {s['games']:>6} {len(s['dates']):>10}  {per_date_summary}"
        )

    # 5. Same (games, different practices) flag -------------------------------
    hdr("5. Players with SAME game count but DIFFERENT practice counts")
    by_games: dict[int, list[tuple[str, int]]] = defaultdict(list)
    for name, s in per_player.items():
        by_games[s["games"]].append((name, len(s["dates"])))
    anomalies = []
    for g, pairs in by_games.items():
        practice_counts = {p for _, p in pairs}
        if len(practice_counts) > 1:
            anomalies.append((g, pairs))
    if not anomalies:
        lines.append("  None -- every game-count bucket has consistent practice counts.")
    else:
        for g, pairs in anomalies:
            lines.append(f"  games={g}:")
            for name, p in sorted(pairs):
                lines.append(f"    {name:<20} practices={p}")

    # 6. Per-date match counts -----------------------------------------------
    hdr("6. Scored matches per date")
    by_date: dict = defaultdict(list)
    for m in scored_matches:
        by_date[m.date].append(m.id)
    for d in sorted(by_date.keys()):
        lines.append(f"  {d}: {len(by_date[d])} match(es)  ids={by_date[d]}")

    return "\n".join(lines)


@router.get("/admin/network", response_class=HTMLResponse)
async def player_network(
    request: Request,
    user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    from collections import defaultdict
    from itertools import combinations

    from sqlalchemy import or_
    matches = (
        db.query(Match)
        .filter(
            or_(
                Match.score_team_a.isnot(None) & Match.score_team_b.isnot(None),
                Match.scores.isnot(None),
            )
        )
        .all()
    )

    edge_data = defaultdict(lambda: {"games": 0, "wins": 0})
    player_ids_seen = set()

    for match in matches:
        scores = match.parsed_scores
        if not scores:
            continue
        max_score = max(scores)
        num_winners = sum(1 for s in scores if s == max_score)

        # Group player IDs by team
        teams_ids: dict[int, list[int]] = defaultdict(list)
        for mp in match.players:
            idx = TEAM_BY_INDEX.index(mp.team)
            teams_ids[idx].append(mp.user_id)

        for idx, team_ids in teams_ids.items():
            won = idx < len(scores) and scores[idx] == max_score and num_winners == 1
            player_ids_seen.update(team_ids)
            for u, v in combinations(sorted(team_ids), 2):
                edge_data[(u, v)]["games"] += 1
                if won:
                    edge_data[(u, v)]["wins"] += 1

    players = (
        db.query(User).filter(User.id.in_(player_ids_seen)).all()
        if player_ids_seen
        else []
    )

    is_admin = user.role == UserRole.ADMIN
    nodes = [
        {
            "id": p.id,
            "name": p.username,
            "true_skill": round(p.true_skill, 2) if (is_admin and p.true_skill is not None) else None,
            "practices": p.number_of_practices or 0,
        }
        for p in players
    ]

    edges = [
        {
            "source": u,
            "target": v,
            "games": data["games"],
            "wins": data["wins"],
            "win_rate": round(data["wins"] / data["games"], 3),
        }
        for (u, v), data in edge_data.items()
    ]

    graph_data = json.dumps({"nodes": nodes, "edges": edges})

    return templates.TemplateResponse(
        request,
        "network_graph.html",
        {
            "user": user,
            "is_admin": is_admin,
            "show_trueskill": is_admin,
            "graph_data": graph_data,
        },
    )


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
