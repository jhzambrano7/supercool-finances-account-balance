"""Unit tests locking in `AccountBalanceContainer`'s one hard constructor rule: `shared` (a
`providers.DependenciesContainer`) cannot carry a working default -- pre-filling it at
class-definition time still gets deep-copied away by `AccountBalanceContainer`'s own
instantiation, the same fork `DependenciesContainer` exists to avoid in the first place. Every
real entrypoint must therefore go through `build_account_balance_container()`, never
`AccountBalanceContainer()` directly.
"""

import pytest
from dependency_injector.errors import Error

from modules.account_balance.adapters.config.dependencies import (
    AccountBalanceContainer,
    build_account_balance_container,
)


def test_constructing_the_container_directly_leaves_shared_undefined() -> None:
    container = AccountBalanceContainer()

    with pytest.raises(Error, match="undefined dependencies"):
        container.check_dependencies()


def test_the_factory_pairs_construction_with_the_override() -> None:
    container = build_account_balance_container()

    container.check_dependencies()  # does not raise
