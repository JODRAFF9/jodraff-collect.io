"""Organisations, comptes utilisateurs et profils enqueteurs."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from apps.api.models.base import Base, IdMixin, TimestampMixin, UserRole


class Organization(Base, IdMixin, TimestampMixin):
    __tablename__ = "organizations"

    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    country: Mapped[str | None] = mapped_column(String(2))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")

    users: Mapped[list[User]] = relationship(back_populates="organization")


class User(Base, IdMixin, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("org_id", "email", name="uq_user_org_email"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32))
    role: Mapped[str] = mapped_column(String(32), default=UserRole.ENUMERATOR.value, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    preferred_language: Mapped[str] = mapped_column(String(8), default="fr")

    organization: Mapped[Organization] = relationship(back_populates="users")
    # ``foreign_keys`` est obligatoire : EnumeratorProfile reference users deux
    # fois (l'enqueteur lui-meme et son superviseur).
    enumerator_profile: Mapped[EnumeratorProfile | None] = relationship(
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
        foreign_keys="EnumeratorProfile.user_id",
    )

    @property
    def user_role(self) -> UserRole:
        return UserRole(self.role)


class EnumeratorProfile(Base, IdMixin, TimestampMixin):
    """Informations terrain d'un enqueteur : matricule, zone, materiel, capacite."""

    __tablename__ = "enumerator_profiles"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    supervisor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    matricule: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    base_zone: Mapped[str | None] = mapped_column(String(120), index=True)
    device_id: Mapped[str | None] = mapped_column(String(64), index=True)
    hired_at: Mapped[date | None] = mapped_column(Date)
    daily_capacity: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    # Modes de collecte que l'enqueteur est habilite a realiser, ex. ["CAPI", "CATI"].
    certified_services: Mapped[list] = mapped_column(default=list)
    training_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    notes: Mapped[str | None] = mapped_column(String(1000))

    user: Mapped[User] = relationship(back_populates="enumerator_profile", foreign_keys=[user_id])
    supervisor: Mapped[User | None] = relationship(foreign_keys=[supervisor_id])


class AuditLog(Base, IdMixin):
    """Journal d'audit : qui a fait quoi, sur quel objet, quand."""

    __tablename__ = "audit_logs"

    org_id: Mapped[str | None] = mapped_column(String(32), index=True)
    actor_id: Mapped[str | None] = mapped_column(String(32), index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
