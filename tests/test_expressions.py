"""L'evaluateur d'expressions execute du code fourni par les utilisateurs :
sa restriction est une exigence de securite, pas un detail d'implementation."""

from __future__ import annotations

import pytest

from apps.api.services.expressions import (
    ExpressionError,
    evaluate,
    evaluate_bool,
    referenced_variables,
    validate_expression,
)


class TestEvaluation:
    def test_arithmetique(self):
        assert evaluate("2 + 3 * 4", {}) == 14

    def test_variables_du_contexte(self):
        assert evaluate("age * 2", {"age": 21}) == 42

    def test_comparaisons_et_logique(self):
        contexte = {"age": 25, "sexe": "F"}
        assert evaluate("age >= 18 and sexe == 'F'", contexte) is True
        assert evaluate("age < 18 or sexe == 'M'", contexte) is False

    def test_appartenance(self):
        assert evaluate("region in ['Dakar', 'Thies']", {"region": "Dakar"}) is True

    def test_expression_conditionnelle(self):
        assert evaluate("'majeur' if age >= 18 else 'mineur'", {"age": 12}) == "mineur"

    def test_division_par_zero_ne_leve_pas(self):
        """En pleine collecte, une division par zero doit degrader, pas planter."""
        assert evaluate("revenu / taille", {"revenu": 100, "taille": 0}) is None


class TestValeursManquantes:
    """Une question non encore posee vaut None : les regles doivent tenir."""

    def test_variable_absente_vaut_none(self):
        assert evaluate("inconnue", {}) is None

    def test_comparaison_avec_valeur_manquante_est_fausse(self):
        assert evaluate("age >= 18", {}) is False

    def test_max_ignore_les_valeurs_manquantes(self):
        assert evaluate("max(taille, 1)", {"taille": None}) == 1
        assert evaluate("max(taille, 1)", {"taille": 5}) == 5

    def test_division_protegee_par_max(self):
        contexte = {"revenu": 600, "taille": None}
        assert evaluate("revenu / max(taille, 1)", contexte) == 600

    def test_arithmetique_avec_manquant_renvoie_none(self):
        assert evaluate("a + b", {"a": 1}) is None


class TestFonctions:
    def test_selected_sur_choix_multiple(self):
        contexte = {"sources": ["salaire", "commerce"]}
        assert evaluate("selected(sources, 'salaire')", contexte) is True
        assert evaluate("selected(sources, 'agriculture')", contexte) is False

    def test_selected_sur_valeur_absente(self):
        assert evaluate("selected(sources, 'x')", {}) is False

    def test_count(self):
        assert evaluate("count(sources)", {"sources": ["a", "b", "c"]}) == 3
        assert evaluate("count(sources)", {}) == 0

    def test_not_empty(self):
        assert evaluate("not_empty(nom)", {"nom": "Awa"}) is True
        assert evaluate("not_empty(nom)", {"nom": ""}) is False


class TestSecurite:
    """Aucune construction permettant l'evasion ne doit passer."""

    @pytest.mark.parametrize(
        "expression",
        [
            "__import__('os').system('echo compromis')",
            "().__class__.__bases__",
            "open('/etc/passwd').read()",
            "eval('1+1')",
            "lambda: 1",
            "[x for x in range(10)]",
            "objet.attribut",
            "tableau[0]",
        ],
    )
    def test_constructions_interdites(self, expression):
        with pytest.raises(ExpressionError):
            evaluate(expression, {})

    def test_fonction_inconnue_rejetee(self):
        with pytest.raises(ExpressionError, match="Fonction inconnue"):
            evaluate("exec('x')", {})

    def test_arguments_nommes_rejetes(self):
        with pytest.raises(ExpressionError):
            evaluate("round(1.5, ndigits=1)", {})

    def test_syntaxe_invalide(self):
        with pytest.raises(ExpressionError, match="Syntaxe invalide"):
            evaluate("age >=", {})


class TestValidationStatique:
    def test_expression_valide(self):
        assert validate_expression("age >= 18", {"age"}) == []

    def test_variable_inconnue_signalee(self):
        erreurs = validate_expression("revenu > 100", {"age"})
        assert any("Variable inconnue" in e for e in erreurs)

    def test_acces_attribut_signale(self):
        assert validate_expression("a.b", {"a"})

    def test_expression_vide_est_valide(self):
        assert validate_expression("", None) == []

    def test_variables_referencees(self):
        assert referenced_variables("age >= 18 and region == 'Dakar'") == {"age", "region"}

    def test_variables_referencees_ignore_les_fonctions(self):
        assert referenced_variables("max(taille, 1)") == {"taille"}


class TestEvaluateBool:
    def test_expression_vide_prend_la_valeur_par_defaut(self):
        assert evaluate_bool(None, {}) is True
        assert evaluate_bool("", {}, default=False) is False

    def test_condition_evaluee(self):
        assert evaluate_bool("age >= 18", {"age": 20}) is True
        assert evaluate_bool("age >= 18", {"age": 10}) is False
