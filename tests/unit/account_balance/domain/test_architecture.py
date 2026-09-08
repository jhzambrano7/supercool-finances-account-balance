"""Architecture tests over the account-balance domain source tree.

These parse `.py` files with `ast` rather than importing them, auditing
structure Python itself cannot enforce (design §4.3, §9.3).
"""

import ast
import re
from pathlib import Path
from typing import Final

_MODULES_ROOT = Path(__file__).resolve().parents[4] / "src" / "modules"
_DOMAIN_ROOT = _MODULES_ROOT / "account_balance" / "domain"


def _iter_module_paths() -> list[Path]:
    return sorted(_MODULES_ROOT.rglob("*.py"))


def _relative_module_name(path: Path) -> str:
    return str(path.relative_to(_MODULES_ROOT)).replace("\\", "/")


def _domain_module_paths() -> list[Path]:
    """Every `.py` file directly under `domain/`, excluding `__init__.py`.

    Not recursive: design §1 defines a flat module layout with no
    sub-packages, so `rglob` is unnecessary here (unlike `_iter_module_paths`,
    which does walk the whole `src/modules` tree for the T7.1 audit).
    """
    return sorted(p for p in _DOMAIN_ROOT.glob("*.py") if p.stem != "__init__")


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
    """design §4.3: a boolean flag could be forwarded from anywhere, but a second *named* method
    must be called by name -- so the audit is this failing test on the next call site, not a
    runtime guard (Python cannot make the name unreachable).


    Expected RED until Work Unit 6 lands `posting.py::revert` -- tasks.md
    T4.8 leaves this deliberately failing; T7.1 completes it once the call
    site exists. Do not weaken this assertion to make it pass early.
    """
    sites = _debit_for_reversal_sites()

    assert sites == {
        "account_balance/domain/account.py": {"def:UserAccount.debit_for_reversal"},
        "account_balance/domain/posting.py": {"call:revert"},
    }


def _is_self_attribute(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


class _InPlaceMutationVisitor(ast.NodeVisitor):
    """Flags any `self.<field> = ...`, `self.<field> += ...` or `object.__setattr__(self, ...)`
    found inside `Account` -- every state-changing method must return a new `Account` instead
    (design §9.3)."""

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


# Design §1's module-layout table, in the strict downward order it defines.
# A module may only import strictly-earlier entries from this tuple (never
# itself, never a later one). Adding a new domain module requires adding its
# name here in the right position -- `test_no_upward_imports` refuses to
# silently skip a module that isn't listed (see the `missing_from_order`
# assertion below), so an omission fails loudly instead of passing by
# accident.
_IMPORT_ORDER: Final = (
    "errors",
    "identifiers",
    "entry",
    "account",
    "transfer",
    "posting",
)


class _ImportedAccountBalanceModulesVisitor(ast.NodeVisitor):
    """Collects what one module imports from within `account_balance`.

    `domain_siblings` holds the bare names of any `domain.<name>` module
    imported (e.g. `"entry"`); `non_domain_layers` holds the first path
    component of any import reaching into `application` or `adapters`
    (design §9.3 item 3 forbids both).
    """

    _PREFIX = "modules.account_balance."

    def __init__(self) -> None:
        self.domain_siblings: set[str] = set()
        self.non_domain_layers: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._record(alias.name, names=None)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module is not None:
            self._record(node.module, names=node.names)
        self.generic_visit(node)

    def _record(self, dotted: str, *, names: list[ast.alias] | None) -> None:
        if not dotted.startswith(self._PREFIX):
            return
        remainder = dotted[len(self._PREFIX) :]
        parts = remainder.split(".")
        component = parts[0]
        if component != "domain":
            self.non_domain_layers.add(component)
            return
        if len(parts) > 1:
            self.domain_siblings.add(parts[1])
        elif names is not None:
            # `from modules.account_balance.domain import <name>` -- the
            # imported names themselves are the sibling modules.
            for alias in names:
                self.domain_siblings.add(alias.name)


def _upward_import_violations() -> dict[str, set[str]]:
    module_paths = _domain_module_paths()
    stems = {path.stem for path in module_paths}
    missing_from_order = stems - set(_IMPORT_ORDER)
    assert not missing_from_order, (
        f"domain module(s) {sorted(missing_from_order)} are not listed in "
        "_IMPORT_ORDER -- add them (in the position design §1 gives them) "
        "before this test can audit their imports"
    )

    violations: dict[str, set[str]] = {}
    for path in module_paths:
        own_index = _IMPORT_ORDER.index(path.stem)
        visitor = _ImportedAccountBalanceModulesVisitor()
        visitor.visit(ast.parse(path.read_text(), filename=str(path)))

        illegal = set(visitor.non_domain_layers)
        for sibling in visitor.domain_siblings:
            if _IMPORT_ORDER.index(sibling) >= own_index:
                illegal.add(sibling)

        if illegal:
            violations[path.stem] = illegal
    return violations


def test_no_upward_imports() -> None:
    """design §1 / §9.3 item 3, D6.

    Import direction inside `domain/` is strictly downward per the §1 table
    (`errors -> identifiers -> entry -> account -> transfer -> posting`): a
    module may import only strictly-earlier modules from that order, never
    itself and never a later one. Nothing under `domain/` may reach into
    `application` or `adapters` either -- the domain has no outward
    dependency on either layer.
    """
    assert _upward_import_violations() == {}


_ID_GENERATING_UUID_CALL: Final = re.compile(r"^uuid\d+$")


def _call_root_name(func: ast.expr) -> str | None:
    """Walks a (possibly chained) call target down to its leftmost name.

    `datetime.now` -> `"datetime"`; `uuid.uuid4` -> `"uuid"`;
    `IdGenerator.generate` -> `"IdGenerator"`; a call with no resolvable
    leading name (e.g. the result of another call) -> `None`.
    """
    node: ast.expr = func
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


class _AmbientCallVisitor(ast.NodeVisitor):
    """Flags calls that mint a timestamp or an id ambiently (design D6).

    Only `ast.Call` nodes are inspected -- a bare mention of the word
    "datetime", "uuid" or "IdGenerator" inside a docstring or comment is a
    string constant to the parser, not a `Call`/`Attribute`/`Name` node, so
    prose explaining the policy (as `identifiers.py` does at length) can
    never trip this visitor.
    """

    def __init__(self) -> None:
        self.violations: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        called_name = (
            func.id
            if isinstance(func, ast.Name)
            else (func.attr if isinstance(func, ast.Attribute) else None)
        )
        root = _call_root_name(func)

        if called_name in {"now", "utcnow"} and root is not None and "datetime" in root.lower():
            self.violations.append(f"datetime.{called_name}")
        elif called_name is not None and _ID_GENERATING_UUID_CALL.fullmatch(called_name):
            self.violations.append(called_name)
        elif called_name == "IdGenerator" or root == "IdGenerator":
            self.violations.append("IdGenerator")

        self.generic_visit(node)


def _ambient_call_violations() -> dict[str, list[str]]:
    violations: dict[str, list[str]] = {}
    for path in _domain_module_paths():
        visitor = _AmbientCallVisitor()
        visitor.visit(ast.parse(path.read_text(), filename=str(path)))
        if visitor.violations:
            violations[path.name] = visitor.violations
    return violations


def test_no_ambient_time_or_id_generation() -> None:
    """design §9.3 item 4, D6.

    The domain never mints its own timestamps or ids -- `Entry.occurred_at`,
    `Transfer.occurred_at` and every identifier field are caller-supplied,
    so the domain stays deterministic and needs no clock/id-generator stub
    to test. This only catches actual call sites (`datetime.now()`,
    `datetime.utcnow()`, `uuid4()`/`uuid7()`/etc., `IdGenerator(...)` or
    `IdGenerator.generate()`); it does not catch, and must not catch,
    `identifiers.py`'s legitimate `from uuid import UUID` (a type, never
    called to generate anything) or its docstrings' prose mentions of
    `IdGenerator`.
    """
    assert _ambient_call_violations() == {}
