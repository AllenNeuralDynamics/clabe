"""Deprecated: resource constraints. Use [`clabe.checks`][clabe.checks] and [`steps.preflight`][clabe.steps.preflight].

| Before | Now |
|---|---|
| ``available_storage_constraint_factory_from_rig(rig, n)`` | ``checks.free_space(n)`` (takes the ``rig``) |
| ``available_storage_constraint_factory(drive, n)`` | ``checks.free_space(n, directory=drive)`` |
| ``remote_dir_exists_constraint_factory(path)`` | ``checks.exists(path)`` |
| ``Constraint(name, predicate, fail_msg_handler=...)`` | ``checks.that(predicate, message)`` or a function that raises |
| ``ResourceMonitor([...]).run()`` | ``steps.preflight(...)`` in a workflow, ``checks.run(...)`` elsewhere |

This package keeps working, built on the checks, and warns when used.
"""

from ._base import ResourceMonitor
from ._constraints import (
    Constraint,
    available_storage_constraint_factory,
    available_storage_constraint_factory_from_rig,
    remote_dir_exists_constraint_factory,
)

__all__ = [
    "Constraint",
    "ResourceMonitor",
    "available_storage_constraint_factory",
    "available_storage_constraint_factory_from_rig",
    "remote_dir_exists_constraint_factory",
]
