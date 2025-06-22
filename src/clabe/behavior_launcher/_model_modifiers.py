from typing import Any, Generic, List, Optional, Protocol, Self, TypeVar

from aind_behavior_services import AindBehaviorRigModel, AindBehaviorSessionModel, AindBehaviorTaskLogicModel

_R = TypeVar("_R", bound=AindBehaviorRigModel, contravariant=True)
_S = TypeVar("_S", bound=AindBehaviorSessionModel, contravariant=True)
_T = TypeVar("_T", bound=AindBehaviorTaskLogicModel, contravariant=True)


class BySubjectModifier(Protocol, Generic[_R, _S, _T]):
    def __call__(
        self, *, rig_schema: Optional[_R], session_schema: Optional[_S], task_logic_schema: Optional[_T], **kwargs: Any
    ) -> None: ...


class BySubjectModifierManager(Generic[_R, _S, _T]):
    def __init__(self: Self, modifier: Optional[List[BySubjectModifier[_R, _S, _T]]] = None) -> None:
        self._modifiers = modifier or []

    def register_modifier(self, modifier: BySubjectModifier[_R, _S, _T]) -> None:
        self._modifiers.append(modifier)

    def apply_modifiers(
        self,
        *,
        rig_schema: Optional[_R] = None,
        session_schema: Optional[_S] = None,
        task_logic_schema: Optional[_T] = None,
        **kwargs: Any,
    ) -> None:
        for modifier in self._modifiers:
            modifier(
                rig_schema=rig_schema, session_schema=session_schema, task_logic_schema=task_logic_schema, **kwargs
            )
