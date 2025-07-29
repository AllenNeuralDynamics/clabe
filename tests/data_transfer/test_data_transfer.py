import os
from datetime import datetime, time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aind_data_schema.core.metadata import CORE_FILES
from aind_watchdog_service.models.manifest_config import BucketType

from clabe.data_mapper.aind_data_schema import AindDataSchemaSessionDataMapper
from clabe.data_transfer.aind_watchdog import (
    ManifestConfig,
    ModalityConfigs,
    WatchConfig,
    WatchdogDataTransferService,
    WatchdogSettings,
)
from clabe.data_transfer.robocopy import RobocopyService, RobocopySettings


@pytest.fixture
def source():
    return Path("source_path")


@pytest.fixture
def aind_data_mapper():
    return MagicMock(spec=AindDataSchemaSessionDataMapper)


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
def watchdog_service(mock_ui_helper, source, settings):
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

    return service


class TestWatchdogDataTransferService:
    def test_initialization(self, watchdog_service, settings):
        assert watchdog_service._settings.destination == settings.destination
        assert watchdog_service._settings.project_name == settings.project_name
        assert watchdog_service._settings.schedule_time == settings.schedule_time
        assert watchdog_service._settings.platform == settings.platform
        assert watchdog_service._settings.capsule_id == settings.capsule_id
        assert watchdog_service._settings.script == settings.script
        assert watchdog_service._settings.s3_bucket == settings.s3_bucket
        assert watchdog_service._settings.mount == settings.mount
        assert watchdog_service._settings.force_cloud_sync == settings.force_cloud_sync
        assert watchdog_service._settings.transfer_endpoint == settings.transfer_endpoint

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
            assert watchdog_service.validate(create_config=False)

    @patch(
        "clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running",
        return_value=False,
    )
    def test_validate_fail(self, mock_is_running, watchdog_service):
        with patch.object(Path, "exists", return_value=False):
            with pytest.raises(FileNotFoundError):
                watchdog_service.validate()

    def test_missing_env_variables(self, source, settings, aind_data_mapper):
        del os.environ["WATCHDOG_EXE"]
        del os.environ["WATCHDOG_CONFIG"]
        with pytest.raises(ValueError):
            WatchdogDataTransferService(
                source,
                settings=settings,
                validate=False,
            ).with_aind_session_data_mapper(aind_data_mapper)

    @patch("clabe.data_transfer.aind_watchdog.Path.exists", return_value=True)
    def test_find_ads_schemas(self, mock_exists):
        source = "mock_source_path"
        expected_files = [Path(source) / f"{file}.json" for file in CORE_FILES]

        result = WatchdogDataTransferService._find_ads_schemas(Path(source))
        assert result == expected_files

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

    def test_add_transfer_service_args_from_factory(self, watchdog_service):
        def modality_configs_factory(watchdog_service: WatchdogDataTransferService):
            return ModalityConfigs(
                modality="behavior-videos",
                source=(Path(watchdog_service._source) / "behavior-videos").as_posix(),
                compress_raw_data=True,
                job_settings={"key": "value"},
            )

        _manifest_config = watchdog_service.add_transfer_service_args(
            watchdog_service._manifest_config, jobs=[modality_configs_factory]
        )

        for job in _manifest_config.transfer_service_args.upload_jobs:
            assert job == _manifest_config.transfer_service_args.upload_jobs[-1]

    def test_add_transfer_service_args_from_instance(self, watchdog_service):
        modality_configs = ModalityConfigs(
            modality="behavior-videos",
            source=(Path(watchdog_service._source) / "behavior-videos").as_posix(),
            compress_raw_data=True,
            job_settings={"key": "value"},  # needs mode to be json, otherwise parent class will raise an error
        )

        _manifest_config = watchdog_service.add_transfer_service_args(
            watchdog_service._manifest_config, jobs=[modality_configs]
        )

        for job in _manifest_config.transfer_service_args.upload_jobs:
            assert job == _manifest_config.transfer_service_args.upload_jobs[-1]

    def test_add_transfer_service_args_fail_on_duplicate_modality(self, watchdog_service):
        def modality_configs_factory(watchdog_service: WatchdogDataTransferService):
            return ModalityConfigs(
                modality="behavior-videos",
                source=(Path(watchdog_service._source) / "behavior-videos").as_posix(),
                compress_raw_data=True,
                job_settings={"key": "value"},
            )

        modality_configs = ModalityConfigs(
            modality="behavior-videos",
            source=(Path(watchdog_service._source) / "behavior-videos").as_posix(),
            job_settings={"key": "value"},  # needs mode to be json, otherwise parent class will raise an error
        )

        with pytest.raises(ValueError):
            _ = watchdog_service.add_transfer_service_args(
                watchdog_service._manifest_config, jobs=[modality_configs_factory, modality_configs]
            )


@pytest.fixture
def robocopy_source():
    return Path("source_path")


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
def robocopy_service(mock_ui_helper, robocopy_source, robocopy_settings):
    return RobocopyService(
        source=robocopy_source,
        settings=robocopy_settings,
        ui_helper=mock_ui_helper,
    )


class TestRobocopyService:
    def test_initialization(self, robocopy_service, robocopy_source, robocopy_settings):
        assert robocopy_service.source == robocopy_source
        assert robocopy_service._settings.destination == robocopy_settings.destination
        assert robocopy_service._settings.log == robocopy_settings.log
        assert robocopy_service._settings.extra_args == robocopy_settings.extra_args
        assert robocopy_service._settings.delete_src
        assert robocopy_service._settings.overwrite
        assert not robocopy_service._settings.force_dir

    def test_transfer(self, mock_ui_helper, robocopy_service):
        with patch("src.clabe.data_transfer.robocopy.subprocess.Popen") as mock_popen:
            mock_ui_helper._prompt_yes_no_question.return_value = True
            mock_process = MagicMock()
            mock_process.wait.return_value = 0
            mock_popen.return_value = mock_process
            robocopy_service.transfer()

    def test_solve_src_dst_mapping_single_path(self, robocopy_service, robocopy_source, robocopy_settings):
        result = robocopy_service._solve_src_dst_mapping(robocopy_source, robocopy_settings.destination)
        assert result == {Path(robocopy_source): Path(robocopy_settings.destination)}

    def test_solve_src_dst_mapping_dict(self, robocopy_service, robocopy_source, robocopy_settings):
        source_dict = {robocopy_source: robocopy_settings.destination}
        result = robocopy_service._solve_src_dst_mapping(source_dict, None)
        assert result == source_dict

    def test_solve_src_dst_mapping_invalid(self, robocopy_service, robocopy_source):
        with pytest.raises(ValueError):
            robocopy_service._solve_src_dst_mapping(robocopy_source, None)
