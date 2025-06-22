from functools import wraps
from typing import Callable, Dict, Generic, List, Optional, TypeVar

_T = TypeVar("_T")

HookObserver = Callable[[_T], None]


class HookObservable(Generic[_T]):
    def __init__(self, hooks: Optional[List[Callable]] = None) -> None:
        hooks = hooks or []
        self._hooks: Dict[Callable, List[HookObserver[_T]]] = {hook: [] for hook in hooks or []}
        self._original_funcs: Dict[Callable, Callable] = {}

    @property
    def hooks(self) -> Dict[Callable, List[HookObserver[_T]]]:
        return self._hooks

    def _ensure_registered_observable(self, hook: Callable) -> None:
        if hook not in self.hooks:
            self.hooks[hook] = []

    def _get_hook_key(self, hook: Callable) -> Callable:
        if hasattr(hook, "__wrapped__"):
            return self._get_hook_key(hook.__wrapped__)
        for wrapper, original in self._original_funcs.items():
            if original == hook:
                return wrapper
        return hook

    def subscribe(self, hook: Callable, observer: HookObserver[_T]) -> None:
        hook_key = self._get_hook_key(hook)
        self._ensure_registered_observable(hook_key)
        self.hooks[hook_key].append(observer)

    def unsubscribe(self, hook: Callable, observer: HookObserver[_T]) -> None:
        hook_key = self._get_hook_key(hook)
        if hook_key in self.hooks:
            self.hooks[hook_key].remove(observer)
        else:
            raise KeyError("Hook not in set.")

    def force_trigger(self, hook: Callable, _args: _T) -> None:
        hook_key = self._get_hook_key(hook)
        if hook_key in self.hooks:
            for observer in self.hooks[hook_key]:
                observer(_args)

    def arm(self, value: _T, *, observers: Optional[List[HookObserver[_T]]] = None) -> Callable:
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                result = func(*args, **kwargs)
                self.force_trigger(wrapper, value)
                return result

            # This is a bit of an hack since wrapped functions do not
            # evaluate to the same object as the bare function
            self._original_funcs[wrapper] = func

            self._ensure_registered_observable(wrapper)
            if observers is not None and len(observers) > 0:
                for observer in observers:
                    self.subscribe(wrapper, observer)

            return wrapper

        return decorator
