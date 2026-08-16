"""Authentification et compte courant."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.db import get_session
from apps.api.deps import current_user
from apps.api.models import User, utcnow
from apps.api.schemas import LoginRequest, TokenResponse
from apps.api.security import create_token, verify_password
from platform_core.config import settings

router = APIRouter(prefix="/api/v1/auth", tags=["Authentification"])


@router.post("/login", response_model=TokenResponse, summary="Ouvrir une session")
def login(payload: LoginRequest, session: Session = Depends(get_session)) -> TokenResponse:
    user = session.scalar(select(User).where(User.email == payload.email.lower().strip()))
    # Message identique dans les deux cas : ne pas reveler l'existence du compte.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Identifiants invalides."
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Compte desactive.")

    user.last_login_at = utcnow()
    session.flush()

    return TokenResponse(
        access_token=create_token(user.id, user.role, user.org_id),
        expires_in=settings.token_ttl_seconds,
        user_id=user.id,
        role=user.role,
        full_name=user.full_name,
    )


@router.get("/me", summary="Profil du compte connecte")
def me(user: User = Depends(current_user)) -> dict:
    profile = user.enumerator_profile
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "org_id": user.org_id,
        "preferred_language": user.preferred_language,
        "enumerator_profile": (
            {
                "matricule": profile.matricule,
                "base_zone": profile.base_zone,
                "daily_capacity": profile.daily_capacity,
                "certified_services": profile.certified_services,
                "training_completed": profile.training_completed,
            }
            if profile
            else None
        ),
    }
