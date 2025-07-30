import re

import pytest

from clabe.launcher._callable_manager import _CallableManager, _Promise, _UnsetType


class TestCallableManager:
    def test_register_and_get_result(self):
        manager = _CallableManager()

        def test_function(value: str):
            return "Hello from " + value

        promise = manager.register(test_function)
        promise.invoke("test_input")
        retrieved_result = manager.get_result(test_function)
        assert retrieved_result == "Hello from test_input"

    def test_get_result_non_existent_callable(self):
        manager = _CallableManager()

        def non_existent_function(value: str):
            pass

        with pytest.raises(KeyError, match="Callable non_existent_function not found in registered promises"):
            manager.get_result(non_existent_function)

    def test_run_multiple_callables(self):
        manager = _CallableManager()

        results = []

        def func_a(value: str):
            results.append(value + "A")
            return value + "A"

        def func_b(value: str):
            results.append(value + "B")
            return value + "B"

        manager.register(func_a)
        manager.register(func_b)

        manager.run("input_")

        assert "input_A" in results
        assert "input_B" in results
        assert manager.get_result(func_a) == "input_A"
        assert manager.get_result(func_b) == "input_B"

    def test_run_only_once(self):
        manager = _CallableManager()
        call_count = 0

        def func_c(value: str):
            nonlocal call_count
            call_count += 1
            return value

        manager.register(func_c)
        manager.run("first")
        manager.run("second")

        assert call_count == 1
        assert manager.get_result(func_c) == "first"

    def test_clear_callables(self):
        manager = _CallableManager()

        def func_d(value: str):
            pass

        manager.register(func_d)
        assert len(manager._callable_promises) == 1
        manager.clear()
        assert len(manager._callable_promises) == 0

    def test_unregister_callable(self):
        manager = _CallableManager()

        def func_e(value: str):
            pass

        promise = manager.register(func_e)
        assert len(manager._callable_promises) == 1
        unregistered_promise = manager.unregister(func_e)
        assert len(manager._callable_promises) == 0
        assert unregistered_promise == promise

    def test_promise_invoke_and_result(self):
        def test_func(x):
            return x * 2

        promise = _Promise(test_func)
        assert not promise.has_result()

        result = promise.invoke(5)
        assert result == 10
        assert promise.has_result()
        assert promise.result == 10

        # Test invoking again returns the same result without re-executing
        result_again = promise.invoke(10)  # Should still return 10, not 20
        assert result_again == 10

    def test_promise_result_before_invoke_raises_error(self):
        def test_func(x):
            return x * 2

        promise = _Promise(test_func)
        with pytest.raises(RuntimeError, match=re.escape("Callable has not been executed yet. Call invoke() first.")):
            promise.result

    def test_unset_type_singleton(self):
        unset1 = _UnsetType()
        unset2 = _UnsetType()
        assert unset1 is unset2

    def test_promise_repr(self):
        def test_func_for_repr(x):
            return x

        promise = _Promise(test_func_for_repr)
        assert repr(promise) == "Promise(func=test_func_for_repr, status=pending)"
        promise.invoke(1)
        assert repr(promise) == "Promise(func=test_func_for_repr, status=executed)"
