#!/usr/bin/env python
"""`alembic upgrade head`, made safe to run during a CloudFormation rollback.

This is the migration task's command in AWS (`infra/stacks/service_stack.py`), rather than plain
`alembic upgrade head`, because of one specific and entirely routine sequence:

1. Deploy `v2`. The migration custom resource applies `v2`'s revisions. The database is now at
   `v2`'s head.
2. The new tasks fail to stabilize -- a bad image, a bug, anything -- and the ECS circuit breaker
   fires.
3. CloudFormation rolls the stack back, which means re-invoking the custom resource with the
   *previous* properties: the `v1` image, running `alembic upgrade head` again.
4. `v1`'s `alembic/versions/` has never heard of the revision now recorded in `alembic_version`.
   Alembic exits with "Can't locate revision identified by …", the custom resource fails, and
   because this is the rollback itself failing, the stack lands in `UPDATE_ROLLBACK_FAILED` --
   which needs `continue-update-rollback --resources-to-skip` from a human before anything can be
   deployed again.

So the rollback of a bad deploy breaks the stack, and it breaks it in the state where recovering is
hardest. The fix is not to downgrade -- an automatic `alembic downgrade` is how a rollback turns
into data loss. It is to recognize that **the database is ahead of this image**, say so loudly, and
exit zero: there is nothing for this image to apply, and forward-only migrations are supposed to be
compatible with the previous version anyway.

Any other failure still exits non-zero and still fails the deployment. The only case treated as
success is the one where there is genuinely nothing to do.
"""

from __future__ import annotations

import logging
import sys

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from modules.shared.adapters.config.settings import Settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("migrate")

_ALEMBIC_INI = "alembic.ini"


def _current_database_revision(database_url: str) -> str | None:
    """What `alembic_version` says, or `None` on a database nothing has migrated yet."""
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def main() -> int:
    config = Config(_ALEMBIC_INI)
    settings = Settings()

    current = _current_database_revision(settings.database_url)
    if current is None:
        logger.info("database has no revision recorded yet; applying everything")
    else:
        scripts = ScriptDirectory.from_config(config)
        known = {revision.revision for revision in scripts.walk_revisions()}
        if current not in known:
            logger.warning(
                "database is at revision %s, which this image does not contain. This is what a "
                "rollback to an older image looks like: there is nothing for this image to apply, "
                "and downgrading automatically is not something a deployment gets to decide. "
                "Leaving the schema untouched.",
                current,
            )
            return 0

    command.upgrade(config, "head")
    logger.info("migrations applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
