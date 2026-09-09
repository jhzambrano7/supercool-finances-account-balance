"""Builds the three stacks once per test session and hands out `Template` objects.

**Why these tests live under `infra/tests/`, with their own `pytest.ini`, rather than the root
`tests/` directory.** `infra/`'s modules import `stacks.*` and `runtime.*`, which only resolve
with `infra/` itself on `sys.path` -- the exact reason the `mypy-infra` pre-commit hook `cd`s into
`infra` before running, and the reason the root `pyproject.toml` excludes `^infra/` from mypy
instead of pointing mypy at it directly. Root `pytest` also hard-codes `testpaths = ["tests"]`, so
putting CDK tests there would either never run under that config or would inherit
`asyncio_mode`/the `integration` marker meant for the service -- neither of which describes a
synth-time check against a CloudFormation template. `infra/pytest.ini` scopes discovery and config
to this directory; run with `cd infra && uv run --group infra pytest`.

**Why assertions on `Template.from_stack(...)` and not a `cdk synth` snapshot file.** A snapshot
goes stale on every `aws-cdk-lib` patch release (logical id hashes, provider-framework internals)
and, when it fails, tells a reader nothing about *why* the diff matters. Each test below names one
decision from `infra/README.md` / `docs/decision-log.md` and asserts the one property that decision
depends on, so a red test says which invariant broke, not just that "the template changed".
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

# Mirrors what `cd infra && uv run --group infra mypy ...` does for the `mypy-infra` hook: put
# `infra/` on the path so `import stacks` / `import runtime` resolve the same way `app.py` does.
# Must happen before the `stacks.*` imports below.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from stacks.data_stack import DataStack
from stacks.environment import network_from_context
from stacks.registry_stack import RegistryStack
from stacks.service_stack import ServiceStack

#: A real-looking but arbitrary tag. What matters for these tests is that it is a *property of the
#: construct*, not its literal value -- see `test_migration_runner.py`.
IMAGE_TAG = "test-1234567"


@pytest.fixture(scope="session")
def stacks() -> Iterator[tuple[RegistryStack, DataStack, ServiceStack]]:
    """The same three stacks `app.py` builds, wired the same way, from a context-less `cdk.App()`.

    No context is passed and no `CDK_DEFAULT_ACCOUNT`/`CDK_DEFAULT_REGION` are read here, which is
    exactly the condition `infra/README.md`'s "Reading it without an account" describes: a reader
    with no AWS credentials and no `cdk.context.json` gets a valid synth. `network_from_context`
    falls back to the placeholder network ids in that case -- `test_synth_without_credentials.py`
    asserts on that behavior directly; every other test file relies on it implicitly by using this
    fixture at all.
    """
    app = cdk.App()
    network = network_from_context(app)
    registry = RegistryStack(app, "AccountBalanceRegistry")
    data = DataStack(app, "AccountBalanceData", network=network)
    service = ServiceStack(
        app,
        "AccountBalanceService",
        network=network,
        database_secret=data.credentials,
        database_security_group_id=data.security_group.security_group_id,
        repository=registry.repository,
        image_tag=IMAGE_TAG,
    )
    service.add_dependency(data)
    service.add_dependency(registry)
    yield registry, data, service


@pytest.fixture(scope="session")
def registry_stack(stacks: tuple[RegistryStack, DataStack, ServiceStack]) -> RegistryStack:
    return stacks[0]


@pytest.fixture(scope="session")
def data_stack(stacks: tuple[RegistryStack, DataStack, ServiceStack]) -> DataStack:
    return stacks[1]


@pytest.fixture(scope="session")
def service_stack(stacks: tuple[RegistryStack, DataStack, ServiceStack]) -> ServiceStack:
    return stacks[2]


@pytest.fixture(scope="session")
def registry_template(registry_stack: RegistryStack) -> Template:
    return Template.from_stack(registry_stack)


@pytest.fixture(scope="session")
def data_template(data_stack: DataStack) -> Template:
    return Template.from_stack(data_stack)


@pytest.fixture(scope="session")
def service_template(service_stack: ServiceStack) -> Template:
    return Template.from_stack(service_stack)
