"""Utilidades para afirmar sobre el código y no sobre los comentarios.

Buscar una cadena prohibida en el texto fuente da falsos positivos justo en los
módulos mejor documentados: un docstring que explica «no se usa `random`»
contiene la palabra `random`. Estas funciones miran el árbol sintáctico, de modo
que las afirmaciones recaen sobre lo que el módulo hace.
"""

import ast
import inspect
from types import ModuleType

_SCOPES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _tree(module: ModuleType) -> ast.Module:
    return ast.parse(inspect.getsource(module))


def imported_names(module: ModuleType) -> set[str]:
    """Módulos y símbolos que `module` importa realmente."""
    names: set[str] = set()
    for node in ast.walk(_tree(module)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
            names.update(alias.name for alias in node.names)
    return names


def code_identifiers(module: ModuleType) -> set[str]:
    """Nombres y atributos que aparecen en el código, sin docstrings ni comentarios."""
    tree = _tree(module)
    identifiers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
    return identifiers


def string_literals(module: ModuleType) -> set[str]:
    """Literales de cadena del código, excluidos los docstrings."""
    tree = _tree(module)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, _SCOPES):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                docstrings.add(doc)
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value not in docstrings
    }
