from typing import TypeVar

from aind_behavior_services import AindBehaviorRigModel, AindBehaviorSessionModel, AindBehaviorTaskLogicModel

TSession = TypeVar("TSession", bound=AindBehaviorSessionModel)
TRig = TypeVar("TRig", bound=AindBehaviorRigModel)
TTaskLogic = TypeVar("TTaskLogic", bound=AindBehaviorTaskLogicModel)
