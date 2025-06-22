from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from ..services import IService

logger = logging.getLogger(__name__)


class ResourceMonitor(IService):
    """
    A service that monitors and validates resource constraints.
    
    This service manages a collection of constraints that can be evaluated to ensure
    that system resources meet the requirements for experiment execution.

    Attributes:
        constraints (List[Constraint]): A list of constraints to monitor
    """

    def __init__(
        self,
        *args,
        constrains: Optional[List[Constraint]] = None,
        **kwargs,
    ) -> None:
        """
        Initializes the ResourceMonitor.

        Args:
            constrains: A list of constraints to initialize with. Defaults to None
        """
        self.constraints = constrains or []

    def validate(self, *args, **kwargs) -> bool:
        """
        Validates all constraints.
        
        Evaluates all registered constraints to determine if system resources
        meet the requirements.

        Returns:
            bool: True if all constraints are satisfied, False otherwise
        """
        return self.evaluate_constraints()

    def add_constraint(self, constraint: Constraint) -> None:
        """
        Adds a new constraint to the monitor.
        
        Registers a new constraint for monitoring with this resource monitor.

        Args:
            constraint: The constraint to add
        """
        self.constraints.append(constraint)

    def remove_constraint(self, constraint: Constraint) -> None:
        """
        Removes a constraint from the monitor.
        
        Unregisters a previously added constraint from monitoring.

        Args:
            constraint: The constraint to remove
        """
        self.constraints.remove(constraint)

    def evaluate_constraints(self) -> bool:
        """
        Evaluates all constraints.
        
        Iterates through all registered constraints and evaluates them, logging
        any failures that occur.

        Returns:
            bool: True if all constraints are satisfied, False otherwise
        """
        for constraint in self.constraints:
            if not constraint():
                logger.error(constraint.on_fail())
                return False
        return True


@dataclass(frozen=True)
class Constraint:
    """
    Represents a resource constraint.
    
    This class encapsulates a constraint function along with its parameters and
    failure handling logic for resource monitoring.

    Attributes:
        name (str): The name of the constraint
        constraint (Callable[..., bool]): The function to evaluate the constraint
        args (List): Positional arguments for the constraint function
        kwargs (dict): Keyword arguments for the constraint function
        fail_msg_handler (Optional[Callable[..., str]]): A function to generate a failure message
    """

    name: str
    constraint: Callable[..., bool]
    args: List = field(default_factory=list)
    kwargs: dict = field(default_factory=dict)
    fail_msg_handler: Optional[Callable[..., str]] = field(default=None)

    def __call__(self) -> bool | Exception:
        """
        Evaluates the constraint.
        
        Executes the constraint function with the stored arguments and returns
        the result of the evaluation.

        Returns:
            bool | Exception: True if the constraint is satisfied, otherwise raises an exception
        """
        return self.constraint(*self.args, **self.kwargs)

    def on_fail(self) -> str:
        """
        Generates a failure message if the constraint is not satisfied.
        
        Uses the registered failure message handler or a default message to
        provide information about constraint failures.

        Returns:
            str: The failure message
        """
        if self.fail_msg_handler:
            return self.fail_msg_handler(*self.args, **self.kwargs)
        else:
            return f"Constraint {self.name} failed."
