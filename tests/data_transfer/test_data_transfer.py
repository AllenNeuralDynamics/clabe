import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aind_behavior_services import AindBehaviorSessionModel
from aind_data_transfer_service.models.core import Task
from requests.exceptions import HTTPError

from clabe.data_transfer.aind_watchdog import (
    ManifestConfig,
    WatchConfig,
    WatchdogDataTransferService,
    WatchdogSettings,
)
from clabe.data_transfer.robocopy import RobocopyService, RobocopySettings
from tests import TESTS_ASSETS

_HAS_ROBOCOPY = shutil.which("robocopy") is not None
_IS_WINDOWS = sys.platform == "win32"


@pytest.fixture
def source():
    """Create a temporary directory with test folder structure."""
    temp_dir = Path(tempfile.mkdtemp(prefix="source_path"))

    folders = ["behavior", "not_a_modality", "behavior-videos"]
    for folder in folders:
        folder_path = temp_dir / folder
        folder_path.mkdir(exist_ok=True)

    # Schema file used by unit tests for _find_schema_candidates
    (temp_dir / "schema.json").write_text("{}", encoding="utf-8")

    yield temp_dir
    import shutil

    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def settings():
    return WatchdogSettings(
        destination=Path("destination_path"),
        schedule_time=time(hour=20),
        project_name="test_project",
        platform="behavior",
        script={"script_key": ["script_value"]},
        s3_bucket="private",
        force_cloud_sync=True,
        transfer_endpoint="http://aind-data-transfer-service-dev/api/v2/submit_jobs",
    )


@pytest.fixture
def mock_session():
    return AindBehaviorSessionModel(
        experiment="mock",
        subject="007",
        session_name="mock_session",
    )


@pytest.fixture
def watchdog_service(source, settings, mock_session):
    os.environ["WATCHDOG_EXE"] = "watchdog.exe"
    os.environ["WATCHDOG_CONFIG"] = str(TESTS_ASSETS / "watch_config.yml")

    service = WatchdogDataTransferService(
        source,
        settings=settings,
        session=mock_session,
        validate=False,
    )

    service._manifest_config = ManifestConfig(
        name="test_manifest",
        modalities={"behavior": ["path/to/behavior"], "behavior-videos": ["path/to/behavior-videos"]},
        subject_id=1,
        acquisition_datetime=datetime(2023, 1, 1, 0, 0, 0),
        schemas=["path/to/schema"],
        destination="path/to/destination",
        project_name="test_project",
        schedule_time=settings.schedule_time,
        transfer_endpoint="http://aind-data-transfer-service-dev/api/v2/submit_jobs",
    )

    service._watch_config = WatchConfig(
        flag_dir="flag_dir",
        manifest_complete="manifest_complete",
    )

    yield service

    # Cleanup
    if "WATCHDOG_EXE" in os.environ:
        del os.environ["WATCHDOG_EXE"]
    if "WATCHDOG_CONFIG" in os.environ:
        del os.environ["WATCHDOG_CONFIG"]


class TestWatchdogDataTransferService:
    @patch("clabe.data_transfer.aind_watchdog.subprocess.check_output")
    def test_is_running(self, mock_check_output, watchdog_service):
        mock_check_output.return_value = (
            "Image Name                     PID Session Name        Session#    Mem Usage\n"
            "========================= ======== ================ =========== ============\n"
            "watchdog.exe                1234 Console                    1    10,000 K\n"
        )
        assert watchdog_service.is_running()

    @patch("clabe.data_transfer.aind_watchdog.subprocess.check_output")
    def test_is_not_running(self, mock_check_output, watchdog_service):
        mock_check_output.return_value = "INFO: No tasks are running which match the specified criteria."
        assert not watchdog_service.is_running()

    @patch("clabe.data_transfer.aind_watchdog.requests.get")
    def test_get_project_names(self, mock_get, watchdog_service):
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.content = '{"data": ["test_project"]}'
        mock_get.return_value = mock_response
        project_names = watchdog_service._get_project_names()
        assert "test_project" in project_names

    @patch("clabe.data_transfer.aind_watchdog.requests.get")
    def test_get_project_names_fail(self, mock_get, watchdog_service):
        mock_response = MagicMock()
        mock_response.ok = False
        mock_get.return_value = mock_response
        with pytest.raises(Exception):
            watchdog_service._get_project_names()

    @patch(
        "clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running",
        return_value=True,
    )
    @patch(
        "clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_valid_project_name",
        return_value=True,
    )
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._read_yaml")
    def test_validate_success(self, mock_read_yaml, mock_is_valid_project_name, mock_is_running, watchdog_service):
        mock_read_yaml.return_value = WatchConfig(
            flag_dir="mock_flag_dir", manifest_complete="manifest_complete_dir"
        ).model_dump()
        with patch.object(Path, "exists", return_value=True):
            assert watchdog_service.validate()

    @patch(
        "clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running",
        return_value=False,
    )
    def test_validate_fail(self, mock_is_running, watchdog_service):
        with patch.object(Path, "exists", return_value=False):
            with pytest.raises(FileNotFoundError):
                watchdog_service.validate()

    def test_missing_env_variables(self, source, settings, mock_session):
        if "WATCHDOG_EXE" in os.environ:
            del os.environ["WATCHDOG_EXE"]
        if "WATCHDOG_CONFIG" in os.environ:
            del os.environ["WATCHDOG_CONFIG"]
        with pytest.raises(ValueError):
            WatchdogDataTransferService(source, settings=settings, validate=False, session=mock_session)

    @patch("clabe.data_transfer.aind_watchdog.Path.mkdir")
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._write_yaml")
    def test_dump_manifest_config(self, mock_write_yaml, mock_mkdir, watchdog_service):
        path = Path("flag_dir/manifest_test_manifest.yaml")
        result = watchdog_service.dump_manifest_config()

        assert isinstance(result, Path)
        assert isinstance(path, Path)
        assert result.resolve() == path.resolve()

        mock_write_yaml.assert_called_once()
        mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)

    @patch("clabe.data_transfer.aind_watchdog.Path.mkdir")
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._write_yaml")
    def test_dump_manifest_config_custom_path(self, mock_write_yaml, mock_mkdir, watchdog_service):
        custom_path = Path("custom_path/manifest_test_manifest.yaml")
        result = watchdog_service.dump_manifest_config(path=custom_path)

        assert isinstance(result, Path)
        assert isinstance(custom_path, Path)
        assert result.resolve() == custom_path.resolve()
        mock_write_yaml.assert_called_once()
        mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)

    def test_dump_manifest_config_no_manifest_config(self, watchdog_service):
        watchdog_service._manifest_config = None

        with pytest.raises(ValueError):
            watchdog_service.dump_manifest_config()

    def test_dump_manifest_config_no_watch_config(self, watchdog_service):
        watchdog_service._watch_config = None

        with pytest.raises(ValueError):
            watchdog_service.dump_manifest_config()

    def test_from_settings_transfer_args(
        self, watchdog_service: WatchdogDataTransferService, settings: WatchdogSettings
    ):
        settings.upload_tasks = {
            "myTask": Task(job_settings={"input_source": "not_interpolated"}),
            "nestedTask": {"nestedTask": Task(job_settings={"input_source": "not_interpolated_nested"})},
            "myTaskInterpolated": Task(job_settings={"input_source": "interpolated/path/{{ destination }}"}),
            "nestedTaskInterpolated": {
                "nestedTask": Task(job_settings={"input_source": "interpolated/path/{{ destination }}/nested"})
            },
        }
        settings.job_type = "not_default"

        manifest = watchdog_service._manifest_config
        assert manifest is not None
        new_watchdog_manifest = watchdog_service._make_transfer_args(
            manifest,
            add_default_tasks=True,
            extra_tasks=settings.upload_tasks or {},
            job_type=settings.job_type,
        )

        transfer_service_args = new_watchdog_manifest.transfer_service_args
        assert transfer_service_args is not None, "Transfer service args are not set"
        tasks = transfer_service_args.upload_jobs[0].tasks
        assert transfer_service_args.upload_jobs[0].job_type == "not_default"
        assert "modality_transformation_settings" in tasks
        assert "gather_preliminary_metadata" in tasks
        assert all(task in tasks for task in ["myTask", "nestedTask", "myTaskInterpolated", "nestedTaskInterpolated"])
        my_task_interpolated = tasks["myTaskInterpolated"]
        assert isinstance(my_task_interpolated, Task)
        assert (
            Path(my_task_interpolated.model_dump()["job_settings"]["input_source"]).resolve()
            == Path(f"interpolated/path/{WatchdogDataTransferService._remote_destination_root(manifest)}").resolve()
        )
        nested_wrapper = tasks["nestedTaskInterpolated"]
        assert isinstance(nested_wrapper, dict)
        nested_task = nested_wrapper["nestedTask"]
        assert isinstance(nested_task, Task)
        assert (
            Path(nested_task.model_dump()["job_settings"]["input_source"]).resolve()
            == Path(
                f"interpolated/path/{WatchdogDataTransferService._remote_destination_root(manifest)}/nested"
            ).resolve()
        )

    def test_make_transfer_args(self, watchdog_service: WatchdogDataTransferService):
        manifest = watchdog_service._manifest_config
        extra_tasks = {
            "myTask": Task(job_settings={"input_source": "not_interpolated"}),
            "nestedTask": {"nestedTask": Task(job_settings={"input_source": "not_interpolated_nested"})},
            "myTaskInterpolated": Task(job_settings={"input_source": "interpolated/path/{{ destination }}"}),
            "nestedTaskInterpolated": {
                "nestedTask": Task(job_settings={"input_source": "interpolated/path/{{ destination }}/nested"})
            },
        }
        assert manifest is not None, "Manifest config is not set"
        new_watchdog_manifest = watchdog_service._make_transfer_args(
            manifest, add_default_tasks=True, extra_tasks=extra_tasks, job_type="not_default"
        )
        transfer_service_args = new_watchdog_manifest.transfer_service_args
        assert transfer_service_args is not None, "Transfer service args are not set"
        tasks = transfer_service_args.upload_jobs[0].tasks
        assert transfer_service_args.upload_jobs[0].job_type == "not_default"
        assert "modality_transformation_settings" in tasks
        assert "gather_preliminary_metadata" in tasks
        assert all(task in tasks for task in ["myTask", "nestedTask", "myTaskInterpolated", "nestedTaskInterpolated"])
        my_task_interpolated = tasks["myTaskInterpolated"]
        assert isinstance(my_task_interpolated, Task)
        assert (
            Path(my_task_interpolated.model_dump()["job_settings"]["input_source"]).resolve()
            == Path(f"interpolated/path/{WatchdogDataTransferService._remote_destination_root(manifest)}").resolve()
        )
        nested_wrapper = tasks["nestedTaskInterpolated"]
        assert isinstance(nested_wrapper, dict)
        nested_task = nested_wrapper["nestedTask"]
        assert isinstance(nested_task, Task)
        assert (
            Path(nested_task.model_dump()["job_settings"]["input_source"]).resolve()
            == Path(
                f"interpolated/path/{WatchdogDataTransferService._remote_destination_root(manifest)}/nested"
            ).resolve()
        )

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running", return_value=False)
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.force_restart", return_value=None)
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.dump_manifest_config")
    def test_transfer_service_not_running_restart_success(
        self,
        mock_dump_manifest_config,
        mock_force_restart,
        mock_is_running,
        watchdog_service,
    ):
        mock_is_running.side_effect = [False, True]  # First call returns False, second returns True
        watchdog_service.transfer()
        mock_force_restart.assert_called_once_with(kill_if_running=False)
        mock_dump_manifest_config.assert_called_once()

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running", return_value=False)
    @patch(
        "clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.force_restart",
        side_effect=subprocess.CalledProcessError(1, "cmd"),
    )
    def test_transfer_service_not_running_restart_fail(self, mock_force_restart, mock_is_running, watchdog_service):
        with pytest.raises(RuntimeError):
            watchdog_service.transfer()
        mock_force_restart.assert_called_once_with(kill_if_running=False)

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running", return_value=True)
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.dump_manifest_config")
    def test_transfer_watch_config_none(
        self,
        mock_dump_manifest_config,
        mock_is_running,
        watchdog_service,
    ):
        watchdog_service._watch_config = None
        with pytest.raises(ValueError):
            watchdog_service.transfer()
        mock_dump_manifest_config.assert_not_called()

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running", return_value=True)
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.dump_manifest_config")
    def test_transfer_success(
        self,
        mock_dump_manifest_config,
        mock_is_running,
        watchdog_service,
    ):
        watchdog_service.transfer()
        mock_dump_manifest_config.assert_called_once()

    @patch("clabe.data_transfer.aind_watchdog.Path.exists", return_value=False)
    def test_validate_executable_not_found(self, mock_exists, watchdog_service):
        with pytest.raises(FileNotFoundError):
            watchdog_service.validate()

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running", return_value=False)
    @patch("clabe.data_transfer.aind_watchdog.Path.exists", return_value=True)
    @patch(
        "clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._read_yaml",
        return_value={"flag_dir": "mock_flag_dir", "manifest_complete": "mock_manifest_complete"},
    )
    def test_validate_service_not_running(self, mock_read_yaml, mock_exists, mock_is_running, watchdog_service):
        assert not watchdog_service.validate()

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_valid_project_name", return_value=False)
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running", return_value=True)
    @patch("clabe.data_transfer.aind_watchdog.Path.exists", return_value=True)
    @patch(
        "clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._read_yaml",
        return_value={"flag_dir": "mock_flag_dir", "manifest_complete": "mock_manifest_complete"},
    )
    def test_validate_invalid_project_name(
        self,
        mock_read_yaml,
        mock_exists,
        mock_is_running,
        mock_is_valid_project_name,
        watchdog_service,
    ):
        assert not watchdog_service.validate()

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_valid_project_name", side_effect=HTTPError)
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running", return_value=True)
    @patch("clabe.data_transfer.aind_watchdog.Path.exists", return_value=True)
    @patch(
        "clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._read_yaml",
        return_value={"flag_dir": "mock_flag_dir", "manifest_complete": "mock_manifest_complete"},
    )
    def test_validate_http_error(
        self,
        mock_read_yaml,
        mock_exists,
        mock_is_running,
        mock_is_valid_project_name,
        watchdog_service,
    ):
        with pytest.raises(HTTPError):
            watchdog_service.validate()

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_valid_project_name", return_value=True)
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running", return_value=True)
    @patch("clabe.data_transfer.aind_watchdog.Path.exists", return_value=True)
    @patch(
        "clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._read_yaml",
        return_value={"flag_dir": "mock_flag_dir", "manifest_complete": "mock_manifest_complete"},
    )
    def test_validate_success_extended(
        self,
        mock_read_yaml,
        mock_exists,
        mock_is_running,
        mock_is_valid_project_name,
        watchdog_service,
    ):
        assert watchdog_service.validate()

    @patch(
        "clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._get_project_names",
        return_value=["test_project"],
    )
    def test_is_valid_project_name_valid(self, mock_get_project_names, watchdog_service):
        assert watchdog_service.is_valid_project_name()

    @patch(
        "clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._get_project_names",
        return_value=["other_project"],
    )
    def test_is_valid_project_name_invalid(self, mock_get_project_names, watchdog_service):
        assert not watchdog_service.is_valid_project_name()

    def test_remote_destination_root(
        self, watchdog_service: WatchdogDataTransferService, mock_session: AindBehaviorSessionModel
    ):
        manifest = watchdog_service._create_manifest_from_session(mock_session)
        root = watchdog_service._remote_destination_root(manifest)
        assert manifest.name is not None
        expected_root = Path(manifest.destination) / manifest.name
        assert root == expected_root

    def test_find_modality_candidates(self, watchdog_service: WatchdogDataTransferService, source: Path):
        candidates = watchdog_service._find_modality_candidates(source)
        assert set(candidates.keys()) == {"behavior", "behavior-videos"}

    def test_find_schema_candidates(self, watchdog_service: WatchdogDataTransferService, source: Path):
        schemas = watchdog_service._find_schema_candidates(source)
        assert any(p.name == "schema.json" for p in schemas)

    def test_interpolate_from_manifest(
        self, watchdog_service: WatchdogDataTransferService, mock_session: AindBehaviorSessionModel
    ):
        watchdog_service._create_manifest_from_session(mock_session)
        tasks = {"custom": Task(job_settings={"input_source": "{{ destination }}/extra"})}
        interpolated = watchdog_service._interpolate_from_manifest(tasks, "replacement/value", "{{ destination }}")
        assert isinstance(interpolated["custom"], Task)
        assert interpolated["custom"].model_dump()["job_settings"]["input_source"].startswith("replacement/value")

    def test_yaml_dump_and_write_read_yaml(
        self,
        watchdog_service: WatchdogDataTransferService,
        mock_session: AindBehaviorSessionModel,
        tmp_path: Path,
    ):
        manifest = watchdog_service._create_manifest_from_session(mock_session)
        yaml_str = watchdog_service._yaml_dump(manifest)
        assert manifest.name is not None
        assert manifest.name in yaml_str
        out_path = tmp_path / "manifest.yaml"
        watchdog_service._write_yaml(manifest, out_path)
        loaded = watchdog_service._read_yaml(out_path)
        assert isinstance(loaded, dict)
        assert loaded.get("name") == manifest.name


@pytest.fixture
def robocopy_temp_dirs(tmp_path):
    """Create temporary source and destination directories for robocopy tests."""
    source_dir = tmp_path / "source"
    dest_dir = tmp_path / "destination"
    source_dir.mkdir()

    (source_dir / "file1.txt").write_text("content1")
    (source_dir / "file2.txt").write_text("content2")
    subdir = source_dir / "subdir"
    subdir.mkdir()
    (subdir / "file3.txt").write_text("content3")
    # Cleanup handled by tmp_path fixture

    yield source_dir, dest_dir


@pytest.fixture
def robocopy_settings():
    return RobocopySettings(
        destination=Path("destination_path"),
        log=Path("log_path"),
        extra_args="/MIR",
        delete_src=True,
        overwrite=True,
        force_dir=False,
    )


@pytest.fixture
def robocopy_service(source, robocopy_settings):
    return RobocopyService(
        source=source,
        settings=robocopy_settings,
    )


class TestRobocopyService:
    def test_initialization(self, robocopy_service, source, robocopy_settings):
        assert robocopy_service.source == source
        assert robocopy_service._settings.destination == robocopy_settings.destination
        assert robocopy_service._settings.log == robocopy_settings.log
        assert robocopy_service._settings.extra_args == robocopy_settings.extra_args
        assert robocopy_service._settings.delete_src
        assert robocopy_service._settings.overwrite
        assert not robocopy_service._settings.force_dir

    def test_transfer_mocked(self, robocopy_service):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="output", stderr="", returncode=0)
            robocopy_service.transfer()
            mock_run.assert_called_once()

    def test_run_mocked(self, robocopy_service):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="output", stderr="", returncode=0)
            result = robocopy_service.run()
            assert result.ok is True
            mock_run.assert_called_once()

    def test_command_single_source(self, robocopy_temp_dirs):
        """Test command building for single source-destination."""
        source_dir, dest_dir = robocopy_temp_dirs
        settings = RobocopySettings(destination=dest_dir, force_dir=False, extra_args="/E")
        service = RobocopyService(source=source_dir, settings=settings)

        cmd = service.command.cmd
        assert cmd[0] == "robocopy"
        assert source_dir.as_posix() in cmd[1]
        assert dest_dir.as_posix() in cmd[2]
        assert "/E" in cmd

    def test_command_dict_multiple_sources(self, tmp_path):
        """Test command building for dict with multiple source-destination pairs."""
        src1 = tmp_path / "src1"
        src2 = tmp_path / "src2"
        dst1 = tmp_path / "dst1"
        dst2 = tmp_path / "dst2"
        src1.mkdir()
        src2.mkdir()

        settings = RobocopySettings(destination=dst1, force_dir=False, extra_args="/E")
        service = RobocopyService(source={src1: dst1, src2: dst2}, settings=settings)

        cmd = service.command.cmd
        # Multiple commands should use cmd /c with & to chain
        assert cmd[0] == "cmd"
        assert cmd[1] == "/c"
        assert "&" in cmd[2]

    def test_validate_without_robocopy(self, robocopy_service):
        """Test validate method behavior."""
        with patch("clabe.data_transfer.robocopy._HAS_ROBOCOPY", False):
            # Reload the service to use the patched value
            service = RobocopyService(source=robocopy_service.source, settings=robocopy_service._settings)
            # The validate method checks the module-level _HAS_ROBOCOPY
            from clabe.data_transfer import robocopy

            original = robocopy._HAS_ROBOCOPY
            robocopy._HAS_ROBOCOPY = False
            try:
                assert not service.validate()
            finally:
                robocopy._HAS_ROBOCOPY = original

    @pytest.mark.skipif(not _IS_WINDOWS or not _HAS_ROBOCOPY, reason="Requires Windows with robocopy")
    def test_transfer_actual_single_source(self, robocopy_temp_dirs):
        """Test actual robocopy execution with single source-destination."""
        from clabe.apps import CommandError

        source_dir, dest_dir = robocopy_temp_dirs
        settings = RobocopySettings(
            destination=dest_dir,
            extra_args="/E /DCOPY:DAT /R:1 /W:1",
            force_dir=True,
        )
        service = RobocopyService(source=source_dir, settings=settings)

        # Robocopy exit codes 0-7 are success, but CommandError is raised for non-zero
        try:
            service.transfer()
        except CommandError as e:
            # Exit codes 1-7 are actually success for robocopy
            assert e.exit_code < 8, f"Robocopy failed with exit code {e.exit_code}"

        # Verify files were copied
        assert (dest_dir / "file1.txt").exists()
        assert (dest_dir / "file1.txt").read_text() == "content1"
        assert (dest_dir / "file2.txt").exists()
        assert (dest_dir / "subdir" / "file3.txt").exists()

    @pytest.mark.skipif(not _IS_WINDOWS or not _HAS_ROBOCOPY, reason="Requires Windows with robocopy")
    def test_transfer_actual_dict_sources(self, tmp_path):
        """Test actual robocopy execution with dict multiple source-destination pairs."""
        from clabe.apps import CommandError

        # Create two source directories
        src1 = tmp_path / "src1"
        src2 = tmp_path / "src2"
        dst1 = tmp_path / "dst1"
        dst2 = tmp_path / "dst2"
        src1.mkdir()
        src2.mkdir()

        # Create files in each source
        (src1 / "from_src1.txt").write_text("source1_content")
        (src2 / "from_src2.txt").write_text("source2_content")

        settings = RobocopySettings(
            destination=Path("not used_in_dict_case"),
            extra_args="/E /DCOPY:DAT /R:1 /W:1",
            force_dir=True,
        )
        service = RobocopyService(source={src1: dst1, src2: dst2}, settings=settings)

        try:
            service.transfer()
        except CommandError as e:
            assert e.exit_code < 8, f"Robocopy failed with exit code {e.exit_code}"

        # Verify files were copied to respective destinations
        assert (dst1 / "from_src1.txt").exists()
        assert (dst1 / "from_src1.txt").read_text() == "source1_content"
        assert (dst2 / "from_src2.txt").exists()
        assert (dst2 / "from_src2.txt").read_text() == "source2_content"

    @pytest.mark.skipif(not _IS_WINDOWS or not _HAS_ROBOCOPY, reason="Requires Windows with robocopy")
    def test_transfer_with_delete_src(self, robocopy_temp_dirs):
        """Test robocopy with delete_src option (move instead of copy)."""
        from clabe.apps import CommandError

        source_dir, dest_dir = robocopy_temp_dirs
        settings = RobocopySettings(
            destination=dest_dir,
            extra_args="/E /R:1 /W:1",
            delete_src=True,
            force_dir=True,
        )
        service = RobocopyService(source=source_dir, settings=settings)

        try:
            service.transfer()
        except CommandError as e:
            assert e.exit_code < 8, f"Robocopy failed with exit code {e.exit_code}"

        # Files should be moved (deleted from source after copy)
        assert (dest_dir / "file1.txt").exists()
        assert not (source_dir / "file1.txt").exists()

    @pytest.mark.skipif(not _IS_WINDOWS or not _HAS_ROBOCOPY, reason="Requires Windows with robocopy")
    def test_transfer_with_overwrite(self, robocopy_temp_dirs):
        """Test robocopy with overwrite option."""
        from clabe.apps import CommandError

        source_dir, dest_dir = robocopy_temp_dirs
        dest_dir.mkdir(exist_ok=True)

        # Create existing file in destination with different content
        (dest_dir / "file1.txt").write_text("old_content")

        settings = RobocopySettings(
            destination=dest_dir,
            extra_args="/E /R:1 /W:1",
            overwrite=True,
            force_dir=True,
        )
        service = RobocopyService(source=source_dir, settings=settings)

        try:
            service.transfer()
        except CommandError as e:
            assert e.exit_code < 8, f"Robocopy failed with exit code {e.exit_code}"

        # File should be overwritten with new content
        assert (dest_dir / "file1.txt").read_text() == "content1"
