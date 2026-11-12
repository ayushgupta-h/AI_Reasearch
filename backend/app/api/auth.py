"""Authentication endpoints (Supabase-backed client configuration)."""

from __future__ import annotations

from typing import Dict

from fastapi import APIRouter, Depends

from app.schemas.responses import AuthConfigResponse, AuthMeResponse
from app.security.dependencies import (
    get_current_user,
    get_supabase_anon_key,
    get_supabase_url,
)

router = APIRouter()


@router.get("/auth/config", response_model=AuthConfigResponse, tags=["Auth"])
async def auth_config() -> AuthConfigResponse:
    """Return Supabase configuration for client-side authentication."""
    return AuthConfigResponse(
        supabaseUrl=get_supabase_url(),
        supabaseAnonKey=get_supabase_anon_key(),
    )


@router.get("/auth/me", response_model=AuthMeResponse, tags=["Auth"])
async def auth_me(current_user: Dict = Depends(get_current_user)) -> AuthMeResponse:
    """Return the profile of the currently authenticated user."""
    return AuthMeResponse(status="authenticated", user=current_user)
