from uuid import UUID

from pydantic import BaseModel, Field


class LoginName(BaseModel):
    name: str


class LoginRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


class AuthState(BaseModel):
    id: UUID
    name: str
    role: str
    must_change_password: bool
    csrf_token: str
