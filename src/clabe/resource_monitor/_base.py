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
        
    Example:
        ```python
        from clabe.resource_monitor import available_storage_constraint_factory
        
        # Create monitor with storage constraint
        monitor = ResourceMonitor()
        storage_constraint = available_storage_constraint_factory(
            drive="C:\\", min_bytes=1e9  # 1GB minimum
        )
        monitor.add_constraint(storage_constraint)
        
        # Validate all constraints
        if monitor.validate():
            print("All constraints satisfied")
        else:
            print("Some constraints failed")
        ```
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
            
        Example:
            ```python
            monitor = ResourceMonitor([storage_constraint, memory_constraint])
            
            if monitor.validate():
                start_experiment()
            else:
                print("Resource requirements not met")
            ```
        """
        return self.evaluate_constraints()

    def add_constraint(self, constraint: Constraint) -> None:
        """
        Adds a new constraint to the monitor.
        
        Registers a new constraint for monitoring with this resource monitor.

        Args:
            constraint: The constraint to add
            
        Example:
            ```python
            monitor = ResourceMonitor()
            
            # Add storage constraint
            storage_constraint = available_storage_constraint_factory()
            monitor.add_constraint(storage_constraint)
            
            # Add custom constraint
            def check_memory():
                return psutil.virtual_memory().available > 1e9
            
            memory_constraint = Constraint(
                name="memory_check",
                constraint=check_memory
            )
            monitor.add_constraint(memory_constraint)
            ```
        """
        self.constraints.append(constraint)

    def remove_constraint(self, constraint: Constraint) -> None:
        """
        Removes a constraint from the monitor.
        
        Unregisters a previously added constraint from monitoring.

        Args:
            constraint: The constraint to remove
            
        Example:
            ```python
            monitor = ResourceMonitor()
            monitor.add_constraint(constraint)
            
            # Later remove it
            monitor.remove_constraint(constraint)
            ```
        """
        self.constraints.remove(constraint)

    def evaluate_constraints(self) -> bool:
        """
        Evaluates all constraints.
        
        Iterates through all registered constraints and evaluates them, logging
        any failures that occur.

        Returns:
            bool: True if all constraints are satisfied, False otherwise
            
        Example:
            ```python
            monitor = ResourceMonitor([constraint1, constraint2])
            
            # Check if all constraints pass
            all_passed = monitor.evaluate_constraints()
            if not all_passed:
                print("Some constraints failed - check logs")
            ```
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
        
    Example:
        ```python
        # Simple constraint
        def check_disk_space(path, min_gb):
            free_gb = shutil.disk_usage(path).free / (1024**3)
            return free_gb >= min_gb
        
        constraint = Constraint(
            name="disk_space_check",
            constraint=check_disk_space,
            kwargs={"path": "C:\\", "min_gb": 10},
            fail_msg_handler=lambda path, min_gb: f"Need {min_gb}GB free on {path}"
        )
        
        simple_constraint = Constraint(
            name="simple_check",
            constraint=lambda x: x > 5)
        
        # Evaluate constraint
        if constraint():
            print("Constraint passed")
        else:
            print(constraint.on_fail())

        # Evaluate simple constraint
        if simple_constraint():
            print("Simple constraint passed")
        else:
            print(simple_constraint.on_fail())
        ```
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
            
        Example:
            ```python
            constraint = Constraint(
                name="test",
                constraint=lambda x: x > 5,
                kwargs={"x": 10}
            )
            
            result = constraint()  # Returns True
            print(f"Constraint passed: {result}")
            ```
        """
        return self.constraint(*self.args, **self.kwargs)

    def on_fail(self) -> str:
        """
        Generates a failure message if the constraint is not satisfied.
        
        Uses the registered failure message handler or a default message to
        provide information about constraint failures.

        Returns:
            str: The failure message
            
        Example:
            ```python
            constraint = Constraint(
                name="memory_check",
                constraint=lambda: False,  # Always fails
                fail_msg_handler=lambda: "Not enough memory available"
            )
            
            if not constraint():
                print(constraint.on_fail())  # "Not enough memory available"
            ```
        """
        if self.fail_msg_handler:
            return self.fail_msg_handler(*self.args, **self.kwargs)
        else:
            return f"Constraint {self.name} failed."
