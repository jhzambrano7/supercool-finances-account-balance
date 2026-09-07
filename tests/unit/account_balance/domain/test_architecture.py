"""Architecture tests over the account-balance domain source tree.

These parse `.py` files with `ast` rather than importing them, auditing
structure Python itself cannot enforce (design §4.3, §9.3).
"""

import ast
from pathlib import Path

_MODULES_ROOT = Path(__file__).resolve().parents[4] / "src" / "modules"


def _iter_module_paths() -> list[Path]:
    return sorted(_MODULES_ROOT.rglob("*.py"))


def _relative_module_name(path: Path) -> str:
    return str(path.relative_to(_MODULES_ROOT)).replace("\\", "/")


class _DebitForReversalVisitor(ast.NodeVisitor):
    """Collects every occurrence of the name `debit_for_reversal` in one module.

    The method *definition* is recorded as `def:<Class>.<method>`; every other
    occurrence (a call, an attribute access, a bare name) is recorded as
    `call:<enclosing function name, or None at module/class level>`.
    """

    def __init__(self) -> None:
        self.occurrences: set[str] = set()
        self._class_stack: list[str] = []
        self._function_stack: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.name == "debit_for_reversal":
            owner = self._class_stack[-1] if self._class_stack else "<module>"
            self.occurrences.add(f"def:{owner}.{node.name}")
        self._function_stack.append(node.name)
        self.generic_visit(node)
        self._function_stack.pop()

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == "debit_for_reversal":
            enclosing = self._function_stack[-1] if self._function_stack else None
            self.occurrences.add(f"call:{enclosing}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id == "debit_for_reversal":
            enclosing = self._function_stack[-1] if self._function_stack else None
            self.occurrences.add(f"call:{enclosing}")
        self.generic_visit(node)


def _debit_for_reversal_sites() -> dict[str, set[str]]:
    sites: dict[str, set[str]] = {}
    for path in _iter_module_paths():
        visitor = _DebitForReversalVisitor()
        visitor.visit(ast.parse(path.read_text(), filename=str(path)))
        if visitor.occurrences:
            sites[_relative_module_name(path)] = visitor.occurrences
    return sites


def test_debit_for_reversal_is_referenced_only_in_definition_and_revert() -> None:
    """design §4.3: a boolean flag could be forwarded from anywhere, but a

    second *named* method must be called by name -- so the audit is this
    failing test on the next call site, not a runtime guard (Python cannot
    make the name unreachable).

    Expected RED until Work Unit 6 lands `posting.py::revert` -- tasks.md
    T4.8 leaves this deliberately failing; T7.1 completes it once the call
    site exists. Do not weaken this assertion to make it pass early.
    """
    sites = _debit_for_reversal_sites()

    assert sites == {
        "account_balance/domain/account.py": {"def:Account.debit_for_reversal"},
        "account_balance/domain/posting.py": {"call:revert"},
    }


def _is_self_attribute(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


class _InPlaceMutationVisitor(ast.NodeVisitor):
    """Flags any `self.<field> = ...`, `self.<field> += ...` or

    `object.__setattr__(self, ...)` found inside `Account` -- every
    state-changing method must return a new `Account` instead (design §9.3).
    """

    def __init__(self) -> None:
        self.violations: list[str] = []
        self._current_method: str | None = None

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        previous = self._current_method
        self._current_method = node.name
        self.generic_visit(node)
        self._current_method = previous

    def visit_Assign(self, node: ast.Assign) -> None:
        if any(_is_self_attribute(target) for target in node.targets):
            self.violations.append(self._current_method or "<module>")
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if _is_self_attribute(node.target):
            self.violations.append(self._current_method or "<module>")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "__setattr__"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "object"
        ):
            self.violations.append(self._current_method or "<module>")
        self.generic_visit(node)


def test_account_has_no_in_place_mutator() -> None:
    path = _MODULES_ROOT / "account_balance" / "domain" / "account.py"
    tree = ast.parse(path.read_text(), filename=str(path))

    visitor = _InPlaceMutationVisitor()
    visitor.visit(tree)

    assert visitor.violations == []
