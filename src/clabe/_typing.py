from typing import TypeVar

from aind_behavior_services import Rig, Session, Task

TSession = TypeVar("TSession", bound=Session)
TRig = TypeVar("TRig", bound=Rig)
TTask = TypeVar("TTask", bound=Task)
