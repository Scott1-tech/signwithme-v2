from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    # bcrypt's hard limit; longer input would have to be truncated, making
    # distinct passwords equivalent.
    password: str = Field(min_length=8, max_length=72)
    full_name: str | None = None
    role: str = "contractor"


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: str | None
    role: str
    is_active: bool
    email_verified: bool = False


class AdminUserCreate(BaseModel):
    """An account made by an admin: the admin vouches for the address."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    full_name: str | None = None
    role: str = "contractor"


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
