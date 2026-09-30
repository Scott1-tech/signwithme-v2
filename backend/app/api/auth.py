from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import AppError, AuthError, ConflictError, NotFoundError
from app.core.security import (
    ROLE_ADMIN,
    client_ip,
    ROLE_RANK,
    create_access_token,
    get_current_user,
    hash_password,
    require_role,
    verify_password,
)
from app.core.throttle import login_throttle
from app.models.user import User
from app.schemas.user import TokenOut, UserCreate, UserLogin, UserOut


class TooManyAttempts(AppError):
    status_code = 429
    code = "too_many_attempts"

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=TokenOut, status_code=201)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    """Self-registration always creates a contractor. Elevated roles are
    assigned by an admin through /api/auth/users/{id}/role."""
    existing = db.scalar(select(User).where(User.email == payload.email.lower()))
    if existing is not None:
        raise ConflictError("An account with that email already exists")

    user = User(
        email=payload.email.lower(),
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        role="contractor",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return TokenOut(access_token=create_access_token(user), user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenOut)
def login(payload: UserLogin, request: Request, db: Session = Depends(get_db)):
    # Throttled per email and source address: an exposed login form is the one
    # endpoint an attacker can hammer without any credentials at all.
    throttle_key = f"{payload.email.lower()}|{client_ip(request) or 'unknown'}"
    locked_for = login_throttle.is_locked(throttle_key)
    if locked_for:
        raise TooManyAttempts(
            f"Too many failed sign-in attempts. Try again in {locked_for} seconds."
        )

    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None or not verify_password(payload.password, user.password_hash):
        login_throttle.record_failure(throttle_key)
        # One message for both cases: distinguishing them confirms which emails
        # have accounts.
        raise AuthError("Incorrect email or password")
    if not user.is_active:
        raise AuthError("Account is disabled")

    login_throttle.reset(throttle_key)
    return TokenOut(access_token=create_access_token(user), user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.post("/users/{user_id}/role", response_model=UserOut)
def set_role(
    user_id: UUID,
    role: str,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role(ROLE_ADMIN)),
):
    if role not in ROLE_RANK:
        raise AuthError(f"Unknown role: {role}")
    target = db.get(User, user_id)
    if target is None:
        raise NotFoundError(f"User {user_id} not found")
    target.role = role
    db.commit()
    db.refresh(target)
    return target
