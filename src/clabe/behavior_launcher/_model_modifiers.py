from typing import Any, Generic, List, Optional, Protocol, Self, TypeVar

from aind_behavior_services import AindBehaviorRigModel, AindBehaviorSessionModel, AindBehaviorTaskLogicModel

_R = TypeVar("_R", bound=AindBehaviorRigModel, contravariant=True)
_S = TypeVar("_S", bound=AindBehaviorSessionModel, contravariant=True)
_T = TypeVar("_T", bound=AindBehaviorTaskLogicModel, contravariant=True)


class BySubjectModifier(Protocol, Generic[_R, _S, _T]):
    """
    Protocol defining the interface for subject-specific schema modifiers.

    This protocol defines a callable that can modify rig, session, and task logic
    schemas based on subject-specific requirements. Implementations should modify
    the schemas in-place as needed.

    Example:
        ```python
        # Define a modifier that sets a field on the session schema
        def my_modifier(rig_schema=None, session_schema=None, task_logic_schema=None, **kwargs):
            if session_schema is not None:
                session_schema.notes = "Modified by subject"
        # Register and use with BySubjectModifierManager
        mgr = BySubjectModifierManager()
        mgr.register_modifier(my_modifier)
        mgr.apply_modifiers(session_schema=some_session)
        ```
    """

    def __call__(
        self, *, rig_schema: Optional[_R], session_schema: Optional[_S], task_logic_schema: Optional[_T], **kwargs: Any
    ) -> None:
        """
        Applies subject-specific modifications to the provided schemas.

        Args:
            rig_schema: Optional rig schema to modify
            session_schema: Optional session schema to modify
            task_logic_schema: Optional task logic schema to modify
            **kwargs: Additional keyword arguments for modifier-specific parameters
        """
        ...


class BySubjectModifierManager(Generic[_R, _S, _T]):
    """
    Manager for applying subject-specific modifications to experiment schemas.

    This class manages a collection of modifiers that can be applied to rig,
    session, and task logic schemas based on subject-specific requirements.

    Attributes:
        _modifiers (List[BySubjectModifier]): List of registered modifier functions

    Example:
        ```python
        # Create a manager and register a modifier
        mgr = BySubjectModifierManager()
        def mod(**kwargs): pass
        mgr.register_modifier(mod)
        # Apply all modifiers to schemas
        mgr.apply_modifiers(rig_schema=rig_schema, session_schema=session_schema, task_logic_schema=task_logic_schema)
        ```
    """

    def __init__(self: Self, modifier: Optional[List[BySubjectModifier[_R, _S, _T]]] = None) -> None:
        """
        Initialize the modifier manager with an optional list of modifiers.

        Args:
            modifier: Optional list of modifier functions to register initially
        """
        self._modifiers = modifier or []

    def register_modifier(self, modifier: BySubjectModifier[_R, _S, _T]) -> None:
        """
        Register a new modifier function with the manager.

        Args:
            modifier: The modifier function to register
        """
        self._modifiers.append(modifier)

    def apply_modifiers(
        self,
        *,
        rig_schema: Optional[_R] = None,
        session_schema: Optional[_S] = None,
        task_logic_schema: Optional[_T] = None,
        **kwargs: Any,
    ) -> None:
        """
        Apply all registered modifiers to the provided schemas.

        Iterates through all registered modifiers and applies them to the schemas
        in the order they were registered.

        Args:
            rig_schema: Optional rig schema to modify
            session_schema: Optional session schema to modify
            task_logic_schema: Optional task logic schema to modify
            **kwargs: Additional keyword arguments passed to all modifiers
        """
        for modifier in self._modifiers:
            modifier(
                rig_schema=rig_schema, session_schema=session_schema, task_logic_schema=task_logic_schema, **kwargs
            )
