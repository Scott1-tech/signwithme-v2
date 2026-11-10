from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.database import get_db
from app.core.exceptions import (
    AppError,
    AuthError,
    ConflictError,
    NotFoundError,
    PermissionError_,
    ValidationError,
)
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
from app.core.throttle import login_email_throttle, login_throttle
from app.models.user import User
from app.schemas.user import AdminUserCreate, TokenOut, UserCreate, UserLogin, UserOut


class TooManyAttempts(AppError):
    status_code = 429
    code = "too_many_attempts"

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=TokenOut, status_code=201)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    """Self-registration always creates a contractor. Elevated roles are
    assigned by an admin through /api/auth/users/{id}/role.

    The new account's e-mail is NOT verified: it can upload and sign its own
    contracts, but a contract addressed to that e-mail by someone else stays
    closed until an admin verifies the address (see `verify_email`)."""
    if not settings.allow_self_registration:
        raise PermissionError_("Registration is closed. Ask an administrator for an account.")
    existing = db.scalar(select(User).where(User.email == payload.email.lower()))
    if existing is not None:
        raise ConflictError("An account with that email already exists")

    user = User(
        email=payload.email.lower(),
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        role="contractor",
        email_verified=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return TokenOut(access_token=create_access_token(user), user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenOut)
def login(payload: UserLogin, request: Request, db: Session = Depends(get_db)):
    # Throttled per email and source address: an exposed login form is the one
    # endpoint an attacker can hammer without any credentials at all.
    email = payload.email.lower()
    throttle_key = f"{email}|{client_ip(request) or 'unknown'}"
    # ...and per e-mail alone, with a higher limit, so a caller who keeps
    # changing address still runs out of guesses at one account.
    locked_for = login_throttle.is_locked(throttle_key) or login_email_throttle.is_locked(email)
    if locked_for:
        raise TooManyAttempts(
            f"Too many failed sign-in attempts. Try again in {locked_for} seconds."
        )

    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(payload.password, user.password_hash):
        login_throttle.record_failure(throttle_key)
        login_email_throttle.record_failure(email)
        # One message for both cases: distinguishing them confirms which emails
        # have accounts.
        raise AuthError("Incorrect email or password")
    if not user.is_active:
        raise AuthError("Account is disabled")

    login_throttle.reset(throttle_key)
    login_email_throttle.reset(email)
    return TokenOut(access_token=create_access_token(user), user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


# --------------------------------------------------------------------------
# Admin: accounts
# --------------------------------------------------------------------------


def _user_or_404(db: Session, user_id: UUID) -> User:
    target = db.get(User, user_id)
    if target is None:
        raise NotFoundError(f"User {user_id} not found")
    return target


@router.get("/users", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db),
    actor: User = Depends(require_role(ROLE_ADMIN)),
):
    """Every account, oldest first -- where an admin finds who still needs
    their e-mail verified."""
    return db.scalars(select(User).order_by(User.created_at, User.email)).all()


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(
    payload: AdminUserCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role(ROLE_ADMIN)),
):
    """An account made by an admin. The admin typed the address, so it is
    verified from the start; this is how accounts are made when
    `ALLOW_SELF_REGISTRATION=false`."""
    if payload.role not in ROLE_RANK:
        raise ValidationError(f"Unknown role: {payload.role}")
    email = payload.email.lower()
    if db.scalar(select(User).where(User.email == email)) is not None:
        raise ConflictError("An account with that email already exists")
    user = User(
        email=email,
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        role=payload.role,
        email_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/users/{user_id}/verify-email", response_model=UserOut)
def verify_email(
    user_id: UUID,
    verified: bool = True,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role(ROLE_ADMIN)),
):
    """Vouch for (or withdraw) an account's e-mail. Only a verified account
    opens contracts addressed to its e-mail by someone else."""
    target = _user_or_404(db, user_id)
    target.email_verified = verified
    db.commit()
    db.refresh(target)
    return target


@router.post("/users/{user_id}/role", response_model=UserOut)
def set_role(
    user_id: UUID,
    role: str,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role(ROLE_ADMIN)),
):
    if role not in ROLE_RANK:
        raise ValidationError(f"Unknown role: {role}")
    target = _user_or_404(db, user_id)
    if target.role == ROLE_ADMIN and role != ROLE_ADMIN:
        # Nothing but the database can undo a deployment with no admin left.
        admins = db.scalar(
            select(func.count()).select_from(User).where(
                User.role == ROLE_ADMIN, User.is_active.is_(True)
            )
        )
        if (admins or 0) <= 1:
            raise ConflictError("This is the only active administrator; promote another one first")
    target.role = role
    db.commit()
    db.refresh(target)
    return target
