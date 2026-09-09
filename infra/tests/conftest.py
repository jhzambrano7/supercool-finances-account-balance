"""Builds the app once per test session, through the same code path `app.py` uses, and hands out
`Template` objects.

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

**Why `build_app()` and not this file re-wiring the stacks by hand.** An earlier version of this
fixture built `RegistryStack`/`DataStack`/`ServiceStack` directly and called
`service.add_dependency(...)` itself -- which meant `test_stack_topology.py`'s dependency
assertion was checking an object *this fixture had just mutated*, a tautology that would stay
green through a real regression in `app.py`. Calling `app.build_app()`, the same function
`__main__` calls, is what makes that class of gap structurally impossible: there is no wiring left
here to duplicate or drift from.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# Mirrors what `cd infra && uv run --group infra mypy ...` does for the `mypy-infra` hook: put
# `infra/` on the path so `import stacks` / `import app` / `import runtime` resolve the same way
# `app.py` does. Must happen before the imports below.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# `runtime.migration_handler` reads its ECS/network wiring from `os.environ` at *import* time
# (`os.environ["CLUSTER_ARN"]`, etc.) and builds a real `boto3` client immediately after -- exactly
# as it does inside the Lambda runtime, via the `environment=` dict `MigrationRunner._function`
# sets. These fake-but-well-formed values live here, once, so every test file that needs the
# module (`test_migration_handler.py`'s unit tests, `test_migration_runner.py`'s cross-file
# deadline invariant) gets them regardless of which file pytest happens to import first --
# `conftest.py` is guaranteed to run before any test module in this directory, unlike relying on
# alphabetical collection order between two unrelated test files.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("CLUSTER_ARN", "arn:aws:ecs:us-east-1:123456789012:cluster/test-cluster")
os.environ.setdefault(
    "TASK_DEFINITION_ARN", "arn:aws:ecs:us-east-1:123456789012:task-definition/migrate-test:1"
)
os.environ.setdefault("SUBNET_IDS", "subnet-aaaaaaaaaaaaaaaaa,subnet-bbbbbbbbbbbbbbbbb")
os.environ.setdefault("SECURITY_GROUP_IDS", "sg-ccccccccccccccccc")
os.environ.setdefault("CONTAINER_NAME", "migrate")

import aws_cdk as cdk
import pytest
from app import AppStacks, build_app
from aws_cdk.assertions import Template

from stacks.data_stack import DataStack
from stacks.registry_stack import RegistryStack
from stacks.service_stack import ServiceStack

#: A real-looking but arbitrary tag, pinned so `test_migration_runner.py` can assert the exact
#: value reaches the custom resource. Deliberately *not* `PLACEHOLDER_IMAGE_TAG`: that value is
#: itself under test in `test_app_entrypoint.py`, against a build that supplies no `imageTag`
#: context at all -- the default `build_app()` would actually use.
IMAGE_TAG = "test-1234567"


def cdk_json_context() -> dict[str, Any]:
    """The feature-flag context `cdk synth` reads from `cdk.json` -- only the CDK CLI populates
    `CDK_CONTEXT_JSON` from this file, so a bare `cdk.App()` here would silently diverge from what
    a real synth produces the day a flag with template impact is added, even though nothing here
    would say so. Loading it explicitly is what keeps "the templates under test" and "the
    templates `cdk synth` emits" the same claim.
    """
    cdk_json = json.loads((Path(__file__).resolve().parent.parent / "cdk.json").read_text())
    context: dict[str, Any] = cdk_json.get("context", {})
    return context


@pytest.fixture(scope="session")
def app_stacks() -> AppStacks:
    """The real `build_app()`, given `cdk.json`'s own context plus a pinned `imageTag` -- not a
    context-less `cdk.App()`, and not hand-rolled stack wiring.
    """
    app = cdk.App(context={**cdk_json_context(), "imageTag": IMAGE_TAG})
    return build_app(app)


@pytest.fixture(scope="session")
def registry_stack(app_stacks: AppStacks) -> RegistryStack:
    return app_stacks.registry


@pytest.fixture(scope="session")
def data_stack(app_stacks: AppStacks) -> DataStack:
    return app_stacks.data


@pytest.fixture(scope="session")
def service_stack(app_stacks: AppStacks) -> ServiceStack:
    return app_stacks.service


@pytest.fixture(scope="session")
def registry_template(registry_stack: RegistryStack) -> Template:
    return Template.from_stack(registry_stack)


@pytest.fixture(scope="session")
def data_template(data_stack: DataStack) -> Template:
    return Template.from_stack(data_stack)


@pytest.fixture(scope="session")
def service_template(service_stack: ServiceStack) -> Template:
    return Template.from_stack(service_stack)
