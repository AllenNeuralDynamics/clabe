import os
import subprocess
from datetime import datetime, time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aind_data_schema.core.session import Session as AdsSession
from aind_watchdog_service.models.manifest_config import BucketType, ManifestConfig, ModalityConfigs
from aind_watchdog_service.models.watch_config import WatchConfig
from requests.exceptions import HTTPError

from clabe.data_mapper.aind_data_schema import AindDataSchemaSessionDataMapper
from clabe.data_transfer.aind_watchdog import (
    WatchdogDataTransferService,
    WatchdogSettings,
)
from clabe.launcher._callable_manager import _Promise


@pytest.fixture
def source():
    return Path("source_path")


@pytest.fixture
def aind_data_mapper():
    mapper = MagicMock(spec=AindDataSchemaSessionDataMapper)
    mapper.is_mapped.return_value = True
    mapper.mapped = MagicMock(spec=AdsSession)
    mapper.mapped.experimenter_full_name = ["John Doe"]
    mapper.mapped.subject_id = "12345"
    mapper.mapped.session_start_time = datetime(2023, 1, 1, 10, 0, 0)
    mapper.mapped.data_streams = [MagicMock()]
    mapper.mapped.data_streams[0].stream_modalities = [MagicMock(abbreviation="behavior")]
    return mapper


@pytest.fixture
def settings():
    return WatchdogSettings(
        destination=Path("destination_path"),
        schedule_time=time(hour=20),
        project_name="test_project",
        platform="behavior",
        capsule_id="capsule_id",
        script={"script_key": ["script_value"]},
        s3_bucket=BucketType.PRIVATE,
        mount="mount_path",
        force_cloud_sync=True,
        transfer_endpoint="http://aind-data-transfer-service/api/v1/submit_jobs",
    )


@pytest.fixture
def service(mock_ui_helper, source, settings):
    os.environ["WATCHDOG_EXE"] = "watchdog.exe"
    os.environ["WATCHDOG_CONFIG"] = "watchdog_config.yml"

    service = WatchdogDataTransferService(
        source,
        settings=settings,
        validate=False,
        ui_helper=mock_ui_helper,
    )

    service._manifest_config = ManifestConfig(
        name="test_manifest",
        modalities={"behavior": ["path/to/behavior"], "behavior-videos": ["path/to/behavior-videos"]},
        subject_id=1,
        acquisition_datetime=datetime(2023, 1, 1, 0, 0, 0),
        schemas=["path/to/schema"],
        destination="path/to/destination",
        mount="mount_path",
        processor_full_name="processor_name",
        project_name="test_project",
        schedule_time=settings.schedule_time,
        platform="behavior",
        capsule_id="capsule_id",
        s3_bucket=BucketType.PRIVATE,
        script={"script_key": ["script_value"]},
        force_cloud_sync=True,
        transfer_endpoint="http://aind-data-transfer-service/api/v1/submit_jobs",
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
    def test_initialization(self, service: WatchdogDataTransferService, settings):
        assert service._settings.destination == settings.destination
        assert service._settings.project_name == settings.project_name
        assert service._settings.schedule_time == settings.schedule_time
        assert service._settings.platform == settings.platform
        assert service._settings.capsule_id == settings.capsule_id
        assert service._settings.script == settings.script
        assert service._settings.s3_bucket == settings.s3_bucket
        assert service._settings.mount == settings.mount
        assert service._settings.force_cloud_sync == settings.force_cloud_sync
        assert service._settings.transfer_endpoint == settings.transfer_endpoint
        assert service.executable_path == Path("watchdog.exe")
        assert service.config_path == Path("watchdog_config.yml")

    def test_aind_session_data_mapper_get(self, service: WatchdogDataTransferService, aind_data_mapper):
        service.with_aind_session_data_mapper(aind_data_mapper)
        assert service.aind_session_data_mapper == aind_data_mapper

    def test_aind_session_data_mapper_get_not_set(self, service: WatchdogDataTransferService):
        service._aind_session_data_mapper = None
        with pytest.raises(ValueError):
            _ = service.aind_session_data_mapper

    def test_with_aind_session_data_mapper(self, service: WatchdogDataTransferService, aind_data_mapper):
        returned_service = service.with_aind_session_data_mapper(aind_data_mapper)
        assert service._aind_session_data_mapper == aind_data_mapper
        assert returned_service == service

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running", return_value=False)
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.force_restart", return_value=None)
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.dump_manifest_config")
    def test_transfer_service_not_running_restart_success(
        self,
        mock_dump_manifest_config,
        mock_force_restart,
        mock_is_running,
        service: WatchdogDataTransferService,
        aind_data_mapper,
    ):
        mock_is_running.side_effect = [False, True]  # First call returns False, second returns True
        service.with_aind_session_data_mapper(aind_data_mapper)
        service.transfer()
        mock_force_restart.assert_called_once_with(kill_if_running=False)
        mock_dump_manifest_config.assert_called_once()

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running", return_value=False)
    @patch(
        "clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.force_restart",
        side_effect=subprocess.CalledProcessError(1, "cmd"),
    )
    def test_transfer_service_not_running_restart_fail(
        self, mock_force_restart, mock_is_running, service: WatchdogDataTransferService, aind_data_mapper
    ):
        service.with_aind_session_data_mapper(aind_data_mapper)
        with pytest.raises(RuntimeError):
            service.transfer()
        mock_force_restart.assert_called_once_with(kill_if_running=False)

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running", return_value=True)
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.dump_manifest_config")
    def test_transfer_data_mapper_not_mapped(
        self, mock_dump_manifest_config, mock_is_running, service: WatchdogDataTransferService, aind_data_mapper
    ):
        aind_data_mapper.is_mapped.return_value = False
        service.with_aind_session_data_mapper(aind_data_mapper)
        with pytest.raises(ValueError):
            service.transfer()
        mock_dump_manifest_config.assert_not_called()

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running", return_value=True)
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.dump_manifest_config")
    def test_transfer_watch_config_none(
        self, mock_dump_manifest_config, mock_is_running, service: WatchdogDataTransferService, aind_data_mapper
    ):
        service._watch_config = None
        service.with_aind_session_data_mapper(aind_data_mapper)
        with pytest.raises(ValueError):
            service.transfer()
        mock_dump_manifest_config.assert_not_called()

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running", return_value=True)
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.dump_manifest_config")
    def test_transfer_success(
        self, mock_dump_manifest_config, mock_is_running, service: WatchdogDataTransferService, aind_data_mapper
    ):
        service.with_aind_session_data_mapper(aind_data_mapper)
        service.transfer()
        mock_dump_manifest_config.assert_called_once()

    @patch("clabe.data_transfer.aind_watchdog.Path.exists", return_value=False)
    def test_validate_executable_not_found(self, mock_exists, service: WatchdogDataTransferService):
        with pytest.raises(FileNotFoundError):
            service.validate()

    @patch("clabe.data_transfer.aind_watchdog.Path.exists")
    def test_validate_config_not_found_no_create(self, mock_exists, service: WatchdogDataTransferService):
        mock_exists.side_effect = [True, False]  # executable exists, config does not
        with pytest.raises(FileNotFoundError):
            service.validate(create_config=False)

    @patch("clabe.data_transfer.aind_watchdog.Path.exists")
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._write_yaml")
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.create_watch_config")
    def test_validate_config_not_found_create(
        self, mock_create_watch_config, mock_write_yaml, mock_exists, service: WatchdogDataTransferService
    ):
        mock_exists.side_effect = [True, False]  # executable exists, config does not
        mock_create_watch_config.return_value = MagicMock(spec=WatchConfig)
        service.validate(create_config=True)
        mock_create_watch_config.assert_called_once()
        mock_write_yaml.assert_called_once()

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running", return_value=False)
    @patch("clabe.data_transfer.aind_watchdog.Path.exists", return_value=True)
    @patch(
        "clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._read_yaml",
        return_value={"flag_dir": "mock_flag_dir", "manifest_complete": "mock_manifest_complete"},
    )
    def test_validate_service_not_running(
        self, mock_exists, mock_is_running, mock_read_yaml, service: WatchdogDataTransferService
    ):
        assert not service.validate()

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
        service: WatchdogDataTransferService,
    ):
        assert not service.validate()

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
        service: WatchdogDataTransferService,
    ):
        with pytest.raises(HTTPError):
            service.validate()

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_valid_project_name", return_value=True)
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running", return_value=True)
    @patch("clabe.data_transfer.aind_watchdog.Path.exists", return_value=True)
    @patch(
        "clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._read_yaml",
        return_value={"flag_dir": "mock_flag_dir", "manifest_complete": "mock_manifest_complete"},
    )
    def test_validate_success(
        self,
        mock_read_yaml,
        mock_exists,
        mock_is_running,
        mock_is_valid_project_name,
        service: WatchdogDataTransferService,
    ):
        assert service.validate()

    @patch("clabe.data_transfer.aind_watchdog.Path.mkdir")
    @patch("clabe.data_transfer.aind_watchdog.Path.exists")
    def test_create_watch_config_create_dir(self, mock_exists, mock_mkdir):
        mock_exists.side_effect = [False, False]
        watched_dir = Path("test_watched_dir")
        manifest_complete_dir = Path("test_manifest_complete_dir")
        config = WatchdogDataTransferService.create_watch_config(watched_dir, manifest_complete_dir, create_dir=True)
        assert isinstance(config, WatchConfig)
        mock_mkdir.assert_called_with(parents=True, exist_ok=True)
        assert mock_mkdir.call_count == 2

    @patch("clabe.data_transfer.aind_watchdog.Path.mkdir")
    @patch("clabe.data_transfer.aind_watchdog.Path.exists", return_value=False)
    def test_create_watch_config_no_create_dir(self, mock_exists, mock_mkdir):
        watched_dir = Path("test_watched_dir")
        manifest_complete_dir = Path("test_manifest_complete_dir")
        config = WatchdogDataTransferService.create_watch_config(watched_dir, manifest_complete_dir, create_dir=False)
        assert isinstance(config, WatchConfig)
        mock_mkdir.assert_not_called()

    @patch(
        "clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._get_project_names",
        return_value=["test_project"],
    )
    def test_is_valid_project_name_valid(self, mock_get_project_names, service: WatchdogDataTransferService):
        assert service.is_valid_project_name()

    @patch(
        "clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._get_project_names",
        return_value=["other_project"],
    )
    def test_is_valid_project_name_invalid(self, mock_get_project_names, service: WatchdogDataTransferService):
        assert not service.is_valid_project_name()

    @patch(
        "clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._get_project_names",
        return_value=["other_project"],
    )
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._find_ads_schemas", return_value=[])
    def test_create_manifest_config_from_ads_session_invalid_project_name(
        self, mock_find_ads_schemas, mock_get_project_names, service: WatchdogDataTransferService, aind_data_mapper
    ):
        service._validate_project_name = True
        with pytest.raises(ValueError):
            service.create_manifest_config_from_ads_session(aind_data_mapper.mapped)

    @patch("clabe.data_transfer.aind_watchdog.aind_watchdog_service.models.make_standard_transfer_args")
    def test_add_transfer_service_args_no_jobs(
        self, mock_make_standard_transfer_args, service: WatchdogDataTransferService
    ):
        mock_modality_config = MagicMock(spec=ModalityConfigs)
        mock_modality_config.modality = "behavior-videos"
        mock_transfer_service_args = MagicMock()
        mock_transfer_service_args.upload_jobs = [MagicMock()]
        mock_transfer_service_args.upload_jobs[0].modalities = [mock_modality_config]
        mock_make_standard_transfer_args.return_value = mock_transfer_service_args
        manifest_config = service.add_transfer_service_args(service._manifest_config, jobs=None)
        assert isinstance(manifest_config, ManifestConfig)
        mock_make_standard_transfer_args.assert_called_once()

    def test_add_transfer_service_args_with_callable_jobs(self, service: WatchdogDataTransferService, source):
        def modality_configs_factory(watchdog_service: WatchdogDataTransferService):
            return ModalityConfigs(
                modality="behavior-videos",
                source=Path("gets/replaced/by/service").as_posix(),
                compress_raw_data=True,
                job_settings={"key": "value"},
            )

        assert service._manifest_config is not None

        manifest_config = service.add_transfer_service_args(service._manifest_config, jobs=[modality_configs_factory])

        assert isinstance(manifest_config, ManifestConfig)
        assert manifest_config.transfer_service_args is not None
        assert (
            len(manifest_config.transfer_service_args.upload_jobs[0].modalities) == 2
        )  # The one we add + the default one added by watchdog
        assert (
            manifest_config.transfer_service_args.upload_jobs[0].modalities[-1].modality.abbreviation
            == "behavior-videos"
        )
        assert manifest_config.transfer_service_args.upload_jobs[0].modalities[-1].job_settings == {"key": "value"}

    def test_add_transfer_service_args_with_instance_jobs(self, service: WatchdogDataTransferService):
        modality_configs = ModalityConfigs(
            modality="behavior-videos",
            source=Path("gets/replaced/by/service").as_posix(),
            job_settings={"key": "value"},
        )

        assert service._manifest_config is not None

        manifest_config = service.add_transfer_service_args(service._manifest_config, jobs=[modality_configs])

        assert isinstance(manifest_config, ManifestConfig)
        assert manifest_config.transfer_service_args is not None
        assert (
            len(manifest_config.transfer_service_args.upload_jobs[0].modalities) == 2
        )  # The one we add + the default one added by watchdog
        assert (
            manifest_config.transfer_service_args.upload_jobs[0].modalities[-1].modality.abbreviation
            == "behavior-videos"
        )
        assert manifest_config.transfer_service_args.upload_jobs[0].modalities[-1].job_settings == {"key": "value"}

    @patch("clabe.data_transfer.aind_watchdog.aind_watchdog_service.models.make_standard_transfer_args")
    def test_add_transfer_service_args_with_submit_job_request_kwargs(
        self, mock_make_standard_transfer_args, service: WatchdogDataTransferService
    ):
        mock_transfer_service_args = MagicMock()
        mock_transfer_service_args.model_copy.return_value = mock_transfer_service_args
        mock_make_standard_transfer_args.return_value = mock_transfer_service_args

        submit_kwargs = {"some_key": "some_value"}
        manifest_config = service.add_transfer_service_args(
            service._manifest_config, submit_job_request_kwargs=submit_kwargs
        )

        mock_make_standard_transfer_args.assert_called_once()
        mock_transfer_service_args.model_copy.assert_called_once_with(update=submit_kwargs)
        assert isinstance(manifest_config, ManifestConfig)

    def test_add_transfer_service_args_fail_on_duplicate_modality(self, service: WatchdogDataTransferService, source):
        def modality_configs_factory(watchdog_service: WatchdogDataTransferService):
            return ModalityConfigs(
                modality="behavior-videos",
                source=(Path(watchdog_service._source) / "behavior-videos").as_posix(),
                compress_raw_data=True,
                job_settings={"key": "value"},
            )

        modality_configs = ModalityConfigs(
            modality="behavior-videos",
            source=(Path(service._source) / "behavior-videos").as_posix(),
            job_settings={"key": "value"},
        )

        with pytest.raises(ValueError):
            _ = service.add_transfer_service_args(
                service._manifest_config, jobs=[modality_configs_factory, modality_configs]
            )

    @patch("clabe.data_transfer.aind_watchdog.Path.exists")
    def test_find_ads_schemas_with_existing_schemas(self, mock_exists):
        mock_exists.side_effect = [True] + [
            False
        ] * 50  # First call False, We will prob not have more then 50 modalities...
        source_path = Path("mock_source")
        result = WatchdogDataTransferService._find_ads_schemas(source_path)
        assert len(result) == 1

    @patch("clabe.data_transfer.aind_watchdog.Path.exists", return_value=False)
    def test_find_ads_schemas_no_existing_schemas(self, mock_exists):
        source_path = Path("mock_source")
        result = WatchdogDataTransferService._find_ads_schemas(source_path)
        assert result == []

    @patch("clabe.data_transfer.aind_watchdog.subprocess.check_output")
    def test_is_running(self, mock_check_output, service: WatchdogDataTransferService):
        mock_check_output.return_value = "Image Name                     PID Session Name        Session#    Mem Usage\n========================= ======== ================ =========== =============\nwatchdog.exe                1234 Console                    1    10,000 K\n"
        assert service.is_running()

    @patch("clabe.data_transfer.aind_watchdog.subprocess.check_output")
    def test_is_not_running(self, mock_check_output, service: WatchdogDataTransferService):
        mock_check_output.return_value = "INFO: No tasks are running which match the specified criteria."
        assert not service.is_running()

    @patch("clabe.data_transfer.aind_watchdog.subprocess.run")
    @patch("clabe.data_transfer.aind_watchdog.subprocess.Popen")
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running")
    def test_force_restart_kill_if_running(
        self, mock_is_running, mock_popen, mock_run, service: WatchdogDataTransferService
    ):
        mock_is_running.side_effect = [True, False]  # First call returns True, second returns False
        service.force_restart(kill_if_running=True)
        mock_run.assert_called_once_with(
            ["taskkill", "/IM", service.executable_path.name, "/F"], shell=True, check=True
        )
        mock_popen.assert_called_once()

    @patch("clabe.data_transfer.aind_watchdog.subprocess.run")
    @patch("clabe.data_transfer.aind_watchdog.subprocess.Popen")
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running", return_value=False)
    def test_force_restart_no_kill(self, mock_is_running, mock_popen, mock_run, service: WatchdogDataTransferService):
        service.force_restart(kill_if_running=False)
        mock_run.assert_not_called()
        mock_popen.assert_called_once()

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._write_yaml")
    @patch("clabe.data_transfer.aind_watchdog.Path.mkdir")
    def test_dump_manifest_config_custom_path(self, mock_mkdir, mock_write_yaml, service: WatchdogDataTransferService):
        custom_path = Path("custom_path/manifest_test_manifest.yaml")
        result = service.dump_manifest_config(path=custom_path)
        assert result.resolve() == custom_path.resolve()
        mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)
        mock_write_yaml.assert_called_once()

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._write_yaml")
    @patch("clabe.data_transfer.aind_watchdog.Path.mkdir")
    def test_dump_manifest_config_default_path(self, mock_mkdir, mock_write_yaml, service: WatchdogDataTransferService):
        service._watch_config = WatchConfig(flag_dir="flag_dir", manifest_complete="manifest_complete")
        service._manifest_config.name = "test_manifest"
        result = service.dump_manifest_config()
        expected_path = Path("flag_dir/manifest_test_manifest.yaml").resolve()
        assert result.resolve() == expected_path
        mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)
        mock_write_yaml.assert_called_once()

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._write_yaml")
    @patch("clabe.data_transfer.aind_watchdog.Path.mkdir")
    def test_dump_manifest_config_prefix_logic(self, mock_mkdir, mock_write_yaml, service: WatchdogDataTransferService):
        service._watch_config = WatchConfig(flag_dir="flag_dir", manifest_complete="manifest_complete")
        service._manifest_config.name = "test_manifest"
        custom_path = Path("custom_path/my_manifest.yaml")
        result = service.dump_manifest_config(path=custom_path)
        expected_path = Path("custom_path/manifest_my_manifest.yaml").resolve()
        assert result.resolve() == expected_path
        mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)
        mock_write_yaml.assert_called_once()

    def test_dump_manifest_config_no_manifest_config(self, service: WatchdogDataTransferService):
        service._manifest_config = None
        with pytest.raises(ValueError):
            service.dump_manifest_config()

    def test_dump_manifest_config_no_watch_config(self, service: WatchdogDataTransferService):
        service._watch_config = None
        with pytest.raises(ValueError):
            service.dump_manifest_config()

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.transfer")
    @patch("clabe.data_transfer.aind_watchdog.Launcher")
    def test_build_runner_callable_aind_session_data_mapper(
        self, MockLauncher, mock_transfer, service: WatchdogDataTransferService, settings
    ):
        mock_launcher = MockLauncher()
        mock_launcher.get_session.return_value = MagicMock(subject="test_subject", session_name="test_session")
        mock_launcher.session_directory = Path("launcher_session_dir")

        def mock_aind_mapper_factory(value) -> AindDataSchemaSessionDataMapper:
            mapper = MagicMock(spec=AindDataSchemaSessionDataMapper)
            mapper.is_mapped.return_value = True
            return mapper

        mock_promise = _Promise(mock_aind_mapper_factory)
        mock_promise.invoke(None)

        runner = WatchdogDataTransferService.build_runner(settings, mock_promise)
        service = runner(mock_launcher)

        assert isinstance(service, WatchdogDataTransferService)
        mock_transfer.assert_called_once()
        assert service._settings.destination == (Path("destination_path") / "test_subject")
        assert service._source == Path("launcher_session_dir")
        assert service._session_name == "test_session"

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.transfer")
    @patch("clabe.data_transfer.aind_watchdog.Launcher")
    def test_build_runner_instance_aind_session_data_mapper(
        self, MockLauncher, mock_transfer, service: WatchdogDataTransferService, settings
    ):
        mock_launcher = MockLauncher()
        mock_launcher.get_session.return_value = MagicMock(subject="test_subject", session_name="test_session")
        mock_launcher.session_directory = Path("launcher_session_dir")

        mock_aind_mapper_instance = MagicMock(spec=AindDataSchemaSessionDataMapper)
        mock_aind_mapper_instance.is_mapped.return_value = True

        runner = WatchdogDataTransferService.build_runner(settings, mock_aind_mapper_instance)
        service = runner(mock_launcher)

        assert isinstance(service, WatchdogDataTransferService)
        mock_transfer.assert_called_once()
        assert service._settings.destination == (Path("destination_path") / "test_subject")
        assert service._source == Path("launcher_session_dir")
        assert service._session_name == "test_session"

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.transfer")
    @patch("clabe.data_transfer.aind_watchdog.Launcher")
    def test_build_runner_data_mapper_not_mapped(self, MockLauncher, mock_transfer, settings):
        mock_launcher = MockLauncher()
        mock_launcher.get_session.return_value = MagicMock(subject="test_subject", session_name="test_session")
        mock_launcher.session_directory = Path("launcher_session_dir")

        mock_aind_mapper_instance = MagicMock(spec=AindDataSchemaSessionDataMapper)
        mock_aind_mapper_instance.is_mapped.return_value = False

        runner = WatchdogDataTransferService.build_runner(settings, mock_aind_mapper_instance)
        with pytest.raises(ValueError):
            runner(mock_launcher)
        mock_transfer.assert_not_called()
