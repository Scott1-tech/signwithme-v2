"""Password hashing, JWT issuance and the current-user dependency."""
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import bcrypt
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.core.database import get_db
from app.core.exceptions import AuthError, PermissionError_, ValidationError
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)

# bcrypt hashes at most 72 bytes and raises on anything longer. Passwords are
# rejected at that length in the schema rather than silently truncated here,
# which would make two different long passwords interchangeable.
BCRYPT_MAX_BYTES = 72

ROLE_ADMIN = "admin"
ROLE_REVIEWER = "reviewer"
ROLE_CONTRACTOR = "contractor"
ROLE_RANK = {ROLE_CONTRACTOR: 0, ROLE_REVIEWER: 1, ROLE_ADMIN: 2}


def _encode(raw: str) -> bytes:
    encoded = raw.encode("utf-8")
    if len(encoded) > BCRYPT_MAX_BYTES:
        raise ValidationError(
            f"Password must be {BCRYPT_MAX_BYTES} bytes or fewer once encoded"
        )
    return encoded


def hash_password(raw: str) -> str:
    return bcrypt.hashpw(_encode(raw), bcrypt.gensalt()).decode("ascii")


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_encode(raw), hashed.encode("ascii"))
    except (ValueError, ValidationError):
        return False


def create_access_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise AuthError("Invalid or expired token") from exc


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise AuthError("Missing bearer token")
    payload = decode_token(credentials.credentials)
    user = db.get(User, UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise AuthError("User not found or inactive")
    return user


def require_role(*roles: str):
    """Dependency factory: allow the given roles, or anything ranked above them."""
    minimum = min(ROLE_RANK[r] for r in roles)

    def _dependency(user: User = Depends(get_current_user)) -> User:
        if ROLE_RANK.get(user.role, -1) < minimum:
            raise PermissionError_(f"Requires one of: {', '.join(roles)}")
        return user

    return _dependency


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
