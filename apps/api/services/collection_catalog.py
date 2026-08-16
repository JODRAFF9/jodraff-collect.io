"""Catalogue des services de collecte proposes au demarrage d'un projet.

C'est la porte d'entree de la plateforme : avant toute conception de
questionnaire, le commanditaire choisit un ou plusieurs modes de collecte.
Ce choix conditionne ensuite :

* les types de questions autorises (pas de photo en CATI, pas de GPS en CAWI) ;
* le besoin ou non d'un reseau d'enqueteurs et donc le module terrain ;
* le mode d'alimentation de la couche bronze du lakehouse ;
* les indicateurs de suivi restitues dans Power BI.

Le catalogue est declaratif et versionne dans le code : il constitue un
contrat stable consomme par l'API, l'interface web et la data platform.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum


class CollectionServiceCode(str, Enum):
    """Codes stables des services de collecte."""

    CAWI = "CAWI"  # Computer Assisted Web Interviewing
    CAPI = "CAPI"  # Computer Assisted Personal Interviewing
    CATI = "CATI"  # Computer Assisted Telephone Interviewing
    PAPI = "PAPI"  # Paper And Pencil Interviewing + saisie
    SMS = "SMS"  # SMS / USSD
    MIXED = "MIXED"  # Dispositif multimode


class Channel(str, Enum):
    WEB = "web"
    MOBILE = "mobile"
    PHONE = "phone"
    PAPER = "paper"
    TELECOM = "telecom"
    MULTI = "multi"


@dataclass(frozen=True)
class Capabilities:
    """Ce que le service sait faire techniquement sur le terrain."""

    offline: bool = False
    geolocation: bool = False
    media_capture: bool = False  # photo, signature, piece jointe
    audio_audit: bool = False  # enregistrement de controle qualite
    call_scheduling: bool = False  # prise de rendez-vous / rappels
    quotas: bool = True
    multilingual: bool = True
    requires_enumerator: bool = False
    self_administered: bool = False
    double_entry: bool = False  # double saisie de controle (PAPI)


@dataclass(frozen=True)
class CollectionService:
    """Description complete d'un service de collecte proposable au client."""

    code: CollectionServiceCode
    name: str
    tagline: str
    description: str
    channel: Channel
    capabilities: Capabilities
    allowed_question_types: tuple[str, ...]
    typical_duration_minutes: int
    cost_index: int  # 1 = tres economique ... 5 = tres couteux
    typical_response_rate: float  # taux de reponse observe usuel
    recommended_for: tuple[str, ...]
    limitations: tuple[str, ...]
    bronze_ingestion: str  # comment les donnees atterrissent en bronze
    setup_checklist: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["code"] = self.code.value
        payload["channel"] = self.channel.value
        return payload


# Types de questions du moteur de questionnaire, regroupes pour lisibilite.
_BASE_TYPES = (
    "text",
    "integer",
    "decimal",
    "date",
    "time",
    "single_choice",
    "multi_choice",
    "scale",
    "note",
    "calculate",
)
_RICH_TYPES = ("geopoint", "photo", "audio", "file", "barcode", "signature")
_SHORT_TYPES = ("text", "integer", "single_choice", "multi_choice", "scale", "note")


CATALOG: tuple[CollectionService, ...] = (
    CollectionService(
        code=CollectionServiceCode.CAWI,
        name="Enquete en ligne (CAWI)",
        tagline="Auto-administre par lien ou e-mail, sans enqueteur.",
        description=(
            "Le repondant remplit lui-meme le questionnaire depuis un navigateur. "
            "Diffusion par lien public, lien nominatif a usage unique, e-mail ou QR code. "
            "Ideal pour les panels, la satisfaction client et les enquetes a grande echelle "
            "sur population connectee."
        ),
        channel=Channel.WEB,
        capabilities=Capabilities(
            quotas=True,
            multilingual=True,
            self_administered=True,
            media_capture=True,
        ),
        allowed_question_types=_BASE_TYPES + ("file", "photo"),
        typical_duration_minutes=12,
        cost_index=1,
        typical_response_rate=0.25,
        recommended_for=(
            "Panels et bases de contacts e-mail",
            "Satisfaction client et NPS",
            "Enquetes internes RH",
            "Grands echantillons a budget contraint",
        ),
        limitations=(
            "Couverture limitee aux populations connectees",
            "Taux de reponse faible sans relance",
            "Pas de controle de l'identite du repondant sans lien nominatif",
        ),
        bronze_ingestion="Ecriture directe API -> table interviews -> extraction incrementale vers bronze",
        setup_checklist=(
            "Importer la base de contacts ou generer un lien public",
            "Parametrer les relances automatiques",
            "Definir les quotas et la regle de fermeture",
        ),
    ),
    CollectionService(
        code=CollectionServiceCode.CAPI,
        name="Enquete en face a face (CAPI)",
        tagline="Enqueteurs equipes de tablettes, fonctionne hors connexion.",
        description=(
            "Un enqueteur administre le questionnaire en presence du repondant sur tablette "
            "ou smartphone. L'application fonctionne hors ligne et se synchronise des qu'un "
            "reseau est disponible. Geolocalisation, photos et audit audio permettent un "
            "controle qualite fort."
        ),
        channel=Channel.MOBILE,
        capabilities=Capabilities(
            offline=True,
            geolocation=True,
            media_capture=True,
            audio_audit=True,
            quotas=True,
            multilingual=True,
            requires_enumerator=True,
        ),
        allowed_question_types=_BASE_TYPES + _RICH_TYPES,
        typical_duration_minutes=45,
        cost_index=5,
        typical_response_rate=0.85,
        recommended_for=(
            "Enquetes menages et budget-consommation",
            "Zones rurales ou faiblement connectees",
            "Questionnaires longs et complexes",
            "Enquetes officielles necessitant une preuve de passage",
        ),
        limitations=(
            "Cout terrain eleve (deplacement, formation, supervision)",
            "Delais de collecte plus longs",
            "Necessite une logistique materielle (tablettes, energie)",
        ),
        bronze_ingestion="Lots de synchronisation hors ligne -> /sync -> journal brut -> bronze",
        setup_checklist=(
            "Constituer et former le reseau d'enqueteurs",
            "Charger l'echantillon et decouper les affectations par zone",
            "Activer le controle qualite : GPS, duree minimale, audit audio",
        ),
    ),
    CollectionService(
        code=CollectionServiceCode.CATI,
        name="Enquete telephonique (CATI)",
        tagline="Plateau d'appels avec gestion des rendez-vous et des rappels.",
        description=(
            "Les enqueteurs appellent les repondants depuis un plateau. La plateforme gere la "
            "file d'appels, les statuts d'issue, les rappels programmes et les rendez-vous. "
            "Bon compromis entre cout, rapidite et taux de reponse."
        ),
        channel=Channel.PHONE,
        capabilities=Capabilities(
            call_scheduling=True,
            audio_audit=True,
            quotas=True,
            multilingual=True,
            requires_enumerator=True,
        ),
        allowed_question_types=_BASE_TYPES,
        typical_duration_minutes=20,
        cost_index=3,
        typical_response_rate=0.45,
        recommended_for=(
            "Enquetes d'opinion et sondages rapides",
            "Rappels de suivi et enquetes longitudinales",
            "Populations dispersees geographiquement",
        ),
        limitations=(
            "Duree d'entretien limitee (fatigue du repondant)",
            "Pas de support visuel ni de question a echelle longue",
            "Necessite une base de numeros de qualite",
        ),
        bronze_ingestion="Saisie temps reel au plateau -> table interviews + paradonnees d'appel -> bronze",
        setup_checklist=(
            "Importer et deduplicater la base de numeros",
            "Definir les plages horaires d'appel et le nombre de tentatives",
            "Parametrer les codes d'issue d'appel",
        ),
    ),
    CollectionService(
        code=CollectionServiceCode.PAPI,
        name="Questionnaire papier (PAPI)",
        tagline="Collecte papier puis saisie controlee, avec double saisie optionnelle.",
        description=(
            "Le questionnaire est administre sur papier puis saisi dans la plateforme par des "
            "operateurs. La double saisie independante et le rapprochement automatique "
            "garantissent la qualite. Solution de repli lorsque le numerique est impossible."
        ),
        channel=Channel.PAPER,
        capabilities=Capabilities(
            quotas=True,
            multilingual=True,
            requires_enumerator=True,
            double_entry=True,
        ),
        allowed_question_types=_BASE_TYPES,
        typical_duration_minutes=40,
        cost_index=4,
        typical_response_rate=0.80,
        recommended_for=(
            "Contextes sans electricite ni reseau",
            "Publics reticents au numerique",
            "Reprise d'historique et archives papier",
        ),
        limitations=(
            "Delai de saisie et risque d'erreur de transcription",
            "Pas de controle de coherence au moment de l'entretien",
            "Cout d'impression, d'acheminement et d'archivage",
        ),
        bronze_ingestion="Saisie operateur (simple ou double) -> rapprochement -> bronze",
        setup_checklist=(
            "Generer la maquette papier depuis le questionnaire publie",
            "Organiser l'atelier de saisie et le controle qualite",
            "Activer la double saisie sur un echantillon de controle",
        ),
    ),
    CollectionService(
        code=CollectionServiceCode.SMS,
        name="Enquete SMS / USSD",
        tagline="Questions courtes envoyees par SMS, reponses par retour.",
        description=(
            "Sequence de questions courtes envoyees par SMS ou session USSD. Tres economique "
            "et tres rapide, adapte au suivi a haute frequence sur telephones basiques."
        ),
        channel=Channel.TELECOM,
        capabilities=Capabilities(quotas=True, multilingual=True, self_administered=True),
        allowed_question_types=_SHORT_TYPES,
        typical_duration_minutes=4,
        cost_index=1,
        typical_response_rate=0.18,
        recommended_for=(
            "Suivi a haute frequence (hebdomadaire, mensuel)",
            "Alertes et enquetes de veille rapide",
            "Zones a faible penetration du smartphone",
        ),
        limitations=(
            "Questionnaire tres court obligatoire",
            "Pas de question ouverte longue ni de media",
            "Dependance a un agregateur telecom",
        ),
        bronze_ingestion="Webhook agregateur -> file de messages -> reconstitution d'entretien -> bronze",
        setup_checklist=(
            "Contractualiser avec l'agregateur SMS et reserver un code court",
            "Limiter le questionnaire a 10 questions fermees",
            "Tester la sequence sur un echantillon pilote",
        ),
    ),
    CollectionService(
        code=CollectionServiceCode.MIXED,
        name="Dispositif multimode",
        tagline="Combinaison sequencee de plusieurs modes pour maximiser la couverture.",
        description=(
            "Plusieurs services sont combines sur un meme echantillon selon une sequence "
            "definie, par exemple CAWI puis relance CATI puis rattrapage CAPI. La plateforme "
            "conserve un questionnaire unique et trace le mode effectif de chaque entretien, "
            "ce qui permet de mesurer et de corriger l'effet de mode."
        ),
        channel=Channel.MULTI,
        capabilities=Capabilities(
            offline=True,
            geolocation=True,
            media_capture=True,
            audio_audit=True,
            call_scheduling=True,
            quotas=True,
            multilingual=True,
            requires_enumerator=True,
        ),
        allowed_question_types=_BASE_TYPES + _RICH_TYPES,
        typical_duration_minutes=30,
        cost_index=4,
        typical_response_rate=0.70,
        recommended_for=(
            "Enquetes officielles a fort enjeu de representativite",
            "Populations heterogenes en equipement",
            "Optimisation du couple cout / taux de reponse",
        ),
        limitations=(
            "Complexite de pilotage et d'analyse (effet de mode)",
            "Necessite un questionnaire concu pour tous les modes",
        ),
        bronze_ingestion="Union des flux de chaque mode, dimension mode conservee jusqu'au gold",
        setup_checklist=(
            "Definir la sequence des modes et les regles de bascule",
            "Concevoir un questionnaire compatible avec le mode le plus contraint",
            "Prevoir l'analyse de l'effet de mode dans le plan de traitement",
        ),
    ),
)


_BY_CODE: dict[str, CollectionService] = {s.code.value: s for s in CATALOG}


def list_services() -> list[CollectionService]:
    """Retourne l'ensemble du catalogue, dans l'ordre de presentation."""
    return list(CATALOG)


def get_service(code: str) -> CollectionService:
    """Retourne un service par son code, insensible a la casse."""
    try:
        return _BY_CODE[code.upper()]
    except KeyError as exc:
        raise KeyError(f"Service de collecte inconnu : {code}") from exc


def exists(code: str) -> bool:
    return code.upper() in _BY_CODE


def allowed_question_types(codes: list[str]) -> set[str]:
    """Intersection des types de questions autorises par tous les modes retenus.

    Un questionnaire multimode doit etre concu pour le mode le plus contraint :
    on prend donc l'intersection et non l'union.
    """
    if not codes:
        return set(_BASE_TYPES + _RICH_TYPES)
    sets = [set(get_service(c).allowed_question_types) for c in codes]
    result = sets[0]
    for s in sets[1:]:
        result &= s
    return result


def requires_enumerator(codes: list[str]) -> bool:
    """Le dispositif mobilise-t-il un reseau d'enqueteurs ?"""
    return any(get_service(c).capabilities.requires_enumerator for c in codes)


def estimate(codes: list[str], sample_size: int) -> dict:
    """Estimation indicative de charge terrain pour un echantillon donne.

    Sert a la page de selection : le commanditaire voit immediatement l'ordre
    de grandeur du cout, du volume de contacts a mobiliser et de la duree.
    """
    if not codes or sample_size <= 0:
        return {}
    services = [get_service(c) for c in codes]
    # Le taux de reponse combine suppose des relances sequencees independantes.
    combined_failure = 1.0
    for s in services:
        combined_failure *= 1 - s.typical_response_rate
    combined_rate = 1 - combined_failure

    avg_duration = sum(s.typical_duration_minutes for s in services) / len(services)
    cost_index = max(s.cost_index for s in services)
    contacts_needed = int(sample_size / combined_rate) if combined_rate else 0
    field_hours = round(sample_size * avg_duration / 60, 1)

    return {
        "sample_size": sample_size,
        "expected_response_rate": round(combined_rate, 3),
        "contacts_to_mobilise": contacts_needed,
        "average_duration_minutes": round(avg_duration, 1),
        "total_field_hours": field_hours,
        "cost_index": cost_index,
        "requires_enumerator": requires_enumerator(codes),
        # Base : 6 entretiens utiles par jour et par enqueteur en CAPI, 12 en CATI.
        "suggested_enumerators": (
            max(1, round(field_hours / (6 * 20))) if requires_enumerator(codes) else 0
        ),
    }
