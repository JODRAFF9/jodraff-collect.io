"""Evaluateur d'expressions du questionnaire.

Les regles de pertinence (``relevance``), de contrainte (``constraint``) et de
calcul (``calculation``) sont saisies par le concepteur sous forme
d'expressions Python restreintes, par exemple :

    relevance   : ``age >= 18 and sexe == 'F'``
    constraint  : ``0 <= value <= 120``
    calculation : ``revenu_total / max(taille_menage, 1)``

L'evaluation passe par une liste blanche d'operations issue de l'AST : ni
appel arbitraire, ni acces aux attributs, ni import. C'est indispensable
puisque ces expressions sont fournies par des utilisateurs et executees
cote serveur a chaque reponse.
"""

from __future__ import annotations

import ast
import operator
from typing import Any

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_CMP_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}

_UNARY_OPS = {
    ast.Not: operator.not_,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _fn_count(value: Any) -> int:
    """Nombre de modalites cochees pour une question a choix multiple."""
    if value is None:
        return 0
    if isinstance(value, (list, tuple, set)):
        return len(value)
    return 1


def _fn_selected(value: Any, code: str) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple, set)):
        return code in value
    return value == code


def _skip_none(fn):
    """Neutralise les valeurs manquantes dans les fonctions d'agregation.

    Une question non encore posee vaut ``None`` : ``max(taille, 1)`` doit
    retourner 1 plutot que de lever une erreur en pleine collecte.
    """

    def wrapper(*args):
        if len(args) == 1 and isinstance(args[0], (list, tuple, set)):
            values = [v for v in args[0] if v is not None]
        else:
            values = [v for v in args if v is not None]
        return fn(values) if values else None

    return wrapper


def _guard_none(fn):
    """Retourne ``None`` si l'argument est manquant, au lieu de lever."""

    def wrapper(value, *rest):
        if value is None:
            return None
        return fn(value, *rest)

    return wrapper


_FUNCTIONS = {
    "min": _skip_none(min),
    "max": _skip_none(max),
    "sum": _skip_none(sum),
    "abs": _guard_none(abs),
    "round": _guard_none(round),
    "len": _guard_none(len),
    "int": _guard_none(int),
    "float": _guard_none(float),
    "str": _guard_none(str),
    "count": _fn_count,
    "selected": _fn_selected,
    "is_empty": lambda v: v is None or v == "" or v == [],
    "not_empty": lambda v: not (v is None or v == "" or v == []),
}


class ExpressionError(ValueError):
    """Expression syntaxiquement invalide ou utilisant une construction interdite."""


class _SafeEvaluator(ast.NodeVisitor):
    def __init__(self, context: dict[str, Any]):
        self.context = context

    def visit(self, node: ast.AST) -> Any:  # noqa: D102
        method = getattr(self, f"visit_{type(node).__name__}", None)
        if method is None:
            raise ExpressionError(f"Construction non autorisee : {type(node).__name__}")
        return method(node)

    # --- litteraux et noms ------------------------------------------------
    def visit_Expression(self, node: ast.Expression) -> Any:
        return self.visit(node.body)

    def visit_Constant(self, node: ast.Constant) -> Any:
        return node.value

    def visit_Name(self, node: ast.Name) -> Any:
        # Une variable inconnue vaut None : une question non encore posee ne
        # doit pas faire echouer l'evaluation, elle rend juste la regle fausse.
        return self.context.get(node.id)

    def visit_List(self, node: ast.List) -> list:
        return [self.visit(e) for e in node.elts]

    def visit_Tuple(self, node: ast.Tuple) -> tuple:
        return tuple(self.visit(e) for e in node.elts)

    def visit_Set(self, node: ast.Set) -> set:
        return {self.visit(e) for e in node.elts}

    # --- operateurs -------------------------------------------------------
    def visit_BinOp(self, node: ast.BinOp) -> Any:
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ExpressionError(f"Operateur non autorise : {type(node.op).__name__}")
        left, right = self.visit(node.left), self.visit(node.right)
        if left is None or right is None:
            return None
        if op in (operator.truediv, operator.floordiv, operator.mod) and right == 0:
            return None
        return op(left, right)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> Any:
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise ExpressionError(f"Operateur unaire non autorise : {type(node.op).__name__}")
        return op(self.visit(node.operand))

    def visit_BoolOp(self, node: ast.BoolOp) -> Any:
        values = [self.visit(v) for v in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        return any(values)

    def visit_Compare(self, node: ast.Compare) -> Any:
        left = self.visit(node.left)
        for op_node, comparator in zip(node.ops, node.comparators, strict=True):
            op = _CMP_OPS.get(type(op_node))
            if op is None:
                raise ExpressionError(f"Comparaison non autorisee : {type(op_node).__name__}")
            right = self.visit(comparator)
            # Comparaison d'ordre avec une valeur manquante : resultat faux.
            if left is None or right is None:
                if op not in (operator.eq, operator.ne, _CMP_OPS[ast.In], _CMP_OPS[ast.NotIn]):
                    return False
            try:
                if not op(left, right):
                    return False
            except TypeError:
                return False
            left = right
        return True

    def visit_IfExp(self, node: ast.IfExp) -> Any:
        return self.visit(node.body) if self.visit(node.test) else self.visit(node.orelse)

    def visit_Call(self, node: ast.Call) -> Any:
        if not isinstance(node.func, ast.Name):
            raise ExpressionError("Seuls les appels de fonctions nommees sont autorises")
        fn = _FUNCTIONS.get(node.func.id)
        if fn is None:
            raise ExpressionError(f"Fonction inconnue : {node.func.id}")
        if node.keywords:
            raise ExpressionError("Les arguments nommes ne sont pas autorises")
        return fn(*[self.visit(a) for a in node.args])


def evaluate(expression: str, context: dict[str, Any]) -> Any:
    """Evalue une expression dans un contexte {code_question: valeur}."""
    if not expression or not expression.strip():
        return None
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"Syntaxe invalide : {exc.msg}") from exc
    return _SafeEvaluator(context).visit(tree)


def evaluate_bool(expression: str | None, context: dict[str, Any], default: bool = True) -> bool:
    """Evalue une condition ; une expression vide vaut ``default``."""
    if not expression:
        return default
    return bool(evaluate(expression, context))


def validate_expression(expression: str, known_variables: set[str] | None = None) -> list[str]:
    """Controle statique d'une expression, utilise a la publication.

    Retourne la liste des erreurs (vide si l'expression est valide).
    """
    errors: list[str] = []
    if not expression or not expression.strip():
        return errors
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        return [f"Syntaxe invalide : {exc.msg}"]

    for node in ast.walk(tree):
        node_type = type(node)
        if node_type in (ast.Attribute, ast.Subscript, ast.Lambda, ast.Await):
            errors.append(f"Construction interdite : {node_type.__name__}")
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                errors.append("Appel de fonction non nomme interdit")
            elif node.func.id not in _FUNCTIONS:
                errors.append(f"Fonction inconnue : {node.func.id}")
        elif isinstance(node, ast.Name) and known_variables is not None:
            reserved = {"value", *_FUNCTIONS}
            if node.id not in known_variables and node.id not in reserved:
                errors.append(f"Variable inconnue : {node.id}")
    return errors


def referenced_variables(expression: str | None) -> set[str]:
    """Codes de questions referencees par une expression."""
    if not expression or not expression.strip():
        return set()
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError:
        return set()
    return {
        n.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Name) and n.id not in _FUNCTIONS and n.id != "value"
    }
