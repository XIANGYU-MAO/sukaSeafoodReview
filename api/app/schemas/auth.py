from uuid import UUID

from pydantic import BaseModel, Field

from app.services.auth import NEW_PASSWORD_MAX_LENGTH, NEW_PASSWORD_MIN_LENGTH


class LoginName(BaseModel):
    name: str


class LoginRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(
        min_length=NEW_PASSWORD_MIN_LENGTH,
        max_length=NEW_PASSWORD_MAX_LENGTH,
    )


class AuthState(BaseModel):
    id: UUID
    name: str
    role: str
    must_change_password: bool
    csrf_token: str
