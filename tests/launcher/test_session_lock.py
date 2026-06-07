import pytest

from clabe.launcher._session_lock import SessionAlreadyRunningError, single_session_lock


class TestSingleSessionLock:
    def test_second_acquisition_is_refused(self, tmp_path):
        lock = tmp_path / "session.lock"
        with single_session_lock(path=lock):
            with pytest.raises(SessionAlreadyRunningError):
                with single_session_lock(path=lock):
                    pass

    def test_lock_is_released_on_exit(self, tmp_path):
        lock = tmp_path / "session.lock"
        with single_session_lock(path=lock):
            pass
        # Re-acquiring must succeed once the first holder exits.
        with single_session_lock(path=lock):
            pass
