"""Fixtures partagees : base isolee, organisation, enquete publiee, client HTTP."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# La configuration doit etre fixee avant tout import applicatif : les modules
# construisent leur moteur de base au chargement.
_TMP = Path(tempfile.mkdtemp(prefix="jodraff-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["LAKE_ROOT"] = str(_TMP / "lake")
os.environ["WAREHOUSE_URL"] = str(_TMP / "warehouse.duckdb")
os.environ["REPORTS_OUTPUT_DIR"] = str(_TMP / "reports")
os.environ["ENVIRONMENT"] = "local"
os.environ["SECRET_KEY"] = "cle-de-test"

from fastapi.testclient import TestClient  # noqa: E402

from apps.api.db import SessionLocal, engine  # noqa: E402
from apps.api.main import app  # noqa: E402
from apps.api.models import (  # noqa: E402
    Base,
    EnumeratorProfile,
    Organization,
    Questionnaire,
    Survey,
    SurveyStatus,
    User,
    UserRole,
)
from apps.api.security import hash_password  # noqa: E402
from apps.api.services import questionnaire as qs  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def session():
    """Session de base nettoyee entre chaque test."""
    with SessionLocal() as db:
        yield db
        db.rollback()

    # Purge complete : les tests partagent le meme fichier SQLite.
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def org(session) -> Organization:
    organization = Organization(code="TEST", name="Organisation de test", country="SN")
    session.add(organization)
    session.commit()
    return organization


@pytest.fixture
def users(session, org) -> dict[str, User]:
    """Un compte par role utile aux tests."""
    accounts = {}
    for role in (
        UserRole.ADMIN,
        UserRole.METHODOLOGIST,
        UserRole.SUPERVISOR,
        UserRole.ANALYST,
        UserRole.ENUMERATOR,
    ):
        account = User(
            org_id=org.id,
            email=f"{role.value}@test.local",
            full_name=f"Compte {role.value}",
            role=role.value,
            password_hash=hash_password("motdepasse123"),
        )
        session.add(account)
        session.flush()
        accounts[role.value] = account

    session.add(
        EnumeratorProfile(
            user_id=accounts[UserRole.ENUMERATOR.value].id,
            matricule="ENQ001",
            base_zone="Dakar",
            daily_capacity=8,
            certified_services=["CAPI", "CATI"],
            training_completed=True,
        )
    )
    session.commit()
    return accounts


def auth_headers(client: TestClient, email: str) -> dict[str, str]:
    """Ouvre une session et retourne l'en-tete d'authentification."""
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": "motdepasse123"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


SIMPLE_SCHEMA = {
    "title": "Questionnaire de test",
    "choice_lists": [
        {
            "code": "oui_non",
            "name": "Oui / Non",
            "choices": [
                {"code": "oui", "label": {"fr": "Oui"}, "order": 1},
                {"code": "non", "label": {"fr": "Non"}, "order": 2},
            ],
        },
        {
            "code": "usages",
            "name": "Usages",
            "choices": [
                {"code": "a", "label": {"fr": "Usage A"}, "order": 1},
                {"code": "b", "label": {"fr": "Usage B"}, "order": 2},
                {"code": "aucun", "label": {"fr": "Aucun"}, "order": 3, "is_exclusive": True},
            ],
        },
    ],
    "sections": [
        {
            "code": "principal",
            "label": {"fr": "Section principale"},
            "order": 1,
            "questions": [
                {
                    "code": "age",
                    "type": "integer",
                    "label": {"fr": "Age"},
                    "order": 1,
                    "required": True,
                    "min": 0,
                    "max": 120,
                    "analysis_role": "mesure",
                },
                {
                    "code": "majeur",
                    "type": "single_choice",
                    "label": {"fr": "Etes-vous majeur ?"},
                    "order": 2,
                    "required": True,
                    "relevance": "age >= 18",
                    "choice_list": "oui_non",
                    "analysis_role": "dimension",
                },
                {
                    "code": "usages",
                    "type": "multi_choice",
                    "label": {"fr": "Usages declares"},
                    "order": 3,
                    "choice_list": "usages",
                    "analysis_role": "dimension",
                },
                {
                    "code": "revenu",
                    "type": "decimal",
                    "label": {"fr": "Revenu"},
                    "order": 4,
                    "constraint": "value >= 0",
                    "constraint_message": {"fr": "Le revenu ne peut pas etre negatif."},
                    "analysis_role": "mesure",
                },
                {
                    "code": "revenu_par_an",
                    "type": "calculate",
                    "label": {"fr": "Revenu annuel"},
                    "order": 5,
                    "calculation": "revenu * 12",
                    "analysis_role": "mesure",
                },
            ],
        }
    ],
}


@pytest.fixture
def survey(session, org, users) -> Survey:
    """Enquete CAPI + CATI avec un questionnaire publie, prete a collecter."""
    record = Survey(
        org_id=org.id,
        code="TEST01",
        title="Enquete de test",
        status=SurveyStatus.DESIGN.value,
        collection_services=["CAPI", "CATI"],
        target_sample=100,
        default_language="fr",
        languages=["fr"],
        created_by=users[UserRole.METHODOLOGIST.value].id,
    )
    session.add(record)
    session.flush()

    questionnaire = Questionnaire(survey_id=record.id, version=1, title="Questionnaire de test")
    session.add(questionnaire)
    session.flush()

    qs.from_schema(session, questionnaire, SIMPLE_SCHEMA)
    qs.publish(session, questionnaire)
    record.status = SurveyStatus.ACTIVE.value
    session.commit()
    return record
