"""Dependances FastAPI : session, utilisateur courant et controle des roles."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from apps.api.db import get_session
from apps.api.models import Survey, User, UserRole
from apps.api.security import TokenError, decode_token


def get_db() -> Session:  # pragma: no cover - alias de lisibilite
    yield from get_session()


def current_user(
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> User:
    """Resout l'utilisateur porteur du jeton ``Authorization: Bearer ...``."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Jeton d'authentification requis.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_token(token)
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    user = session.get(User, payload["sub"])
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Compte inconnu ou desactive.")
    return user


def require_roles(*roles: UserRole) -> Callable[[User], User]:
    """Fabrique une dependance restreignant l'acces a certains roles."""
    allowed = {r.value for r in roles}

    def _guard(user: User = Depends(current_user)) -> User:
        if user.role not in allowed and user.role != UserRole.ADMIN.value:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role requis : {', '.join(sorted(allowed))}. Role actuel : {user.role}.",
            )
        return user

    return _guard


def get_survey(
    survey_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> Survey:
    """Charge une enquete en verifiant l'appartenance a l'organisation."""
    survey = session.get(Survey, survey_id)
    if survey is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Enquete introuvable.")
    if survey.org_id != user.org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Enquete hors de votre organisation.")
    return survey
