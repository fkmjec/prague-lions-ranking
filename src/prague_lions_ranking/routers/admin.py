from datetime import timedelta
import json

from fastapi import APIRouter, Depends, HTTPException, status, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from prague_lions_ranking.database import get_db
from prague_lions_ranking.models import User, UserRole, Match, MatchPlayer, Team, Pod, PodPlayer
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

    final_type = match_type_other.strip() if match_type == "Other" else match_type
    if not final_type:
        final_type = "Other"

    # Create the match with temporary ordering (will be fixed below)
    match = Match(date=match_date_parsed, ordering=0, match_type=final_type, notes=notes if notes else None)
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
    from sqlalchemy import or_
    players = (
        db.query(User)
        .filter(User.true_skill.isnot(None))
        .filter(or_(User.number_of_practices >= 3, User.number_of_games >= 5))
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
            "worst_teammate": worst_teammate,
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


@router.get("/api/my-rating-history")
async def my_rating_history(
    user: User = Depends(require_login),
):
    history = json.loads(user.rating_history) if user.rating_history else []
    return JSONResponse(content=history)


@router.get("/admin/network", response_class=HTMLResponse)
async def player_network(
    request: Request,
    user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    from collections import defaultdict
    from itertools import combinations

    matches = (
        db.query(Match)
        .filter(Match.score_team_a.isnot(None), Match.score_team_b.isnot(None))
        .all()
    )

    edge_data = defaultdict(lambda: {"games": 0, "wins": 0})
    player_ids_seen = set()

    for match in matches:
        a_won = match.score_team_a > match.score_team_b
        b_won = match.score_team_b > match.score_team_a

        team_a_ids = [mp.user_id for mp in match.players if mp.team == Team.TEAM_A]
        team_b_ids = [mp.user_id for mp in match.players if mp.team == Team.TEAM_B]

        for team_ids, won in [(team_a_ids, a_won), (team_b_ids, b_won)]:
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

    nodes = [
        {
            "id": p.id,
            "name": p.username,
            "true_skill": round(p.true_skill, 2) if p.true_skill is not None else None,
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

    is_admin = user.role == UserRole.ADMIN
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
