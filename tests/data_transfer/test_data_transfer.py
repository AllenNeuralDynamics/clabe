import os
import unittest
from datetime import datetime, time
from pathlib import Path
from unittest.mock import MagicMock, patch

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

from ..fixtures import MockUiHelper


class TestWatchdogDataTransferService(unittest.TestCase):
    def setUp(self):
        os.environ["WATCHDOG_EXE"] = "watchdog.exe"
        os.environ["WATCHDOG_CONFIG"] = "watchdog_config.yml"
        self.source = Path("source_path")
        self.validate = False
        self.aind_data_mapper = MagicMock(spec=AindDataSchemaSessionDataMapper)
        self.settings = WatchdogSettings(
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

        self.service = WatchdogDataTransferService(
            self.source,
            settings=self.settings,
            validate=self.validate,
            ui_helper=MockUiHelper(),
        )

        self.service._manifest_config = ManifestConfig(
            name="test_manifest",
            modalities={"behavior": ["path/to/behavior"], "behavior-videos": ["path/to/behavior-videos"]},
            subject_id=1,
            acquisition_datetime=datetime(2023, 1, 1, 0, 0, 0),
            schemas=["path/to/schema"],
            destination="path/to/destination",
            mount="mount_path",
            processor_full_name="processor_name",
            project_name="test_project",
            schedule_time=self.settings.schedule_time,
            platform="behavior",
            capsule_id="capsule_id",
            s3_bucket=BucketType.PRIVATE,
            script={"script_key": ["script_value"]},
            force_cloud_sync=True,
            transfer_endpoint="http://aind-data-transfer-service/api/v1/submit_jobs",
        )

        self.service._watch_config = WatchConfig(
            flag_dir="flag_dir",
            manifest_complete="manifest_complete",
        )

    def test_initialization(self):
        self.assertEqual(self.service._settings.destination, self.settings.destination)
        self.assertEqual(self.service._settings.project_name, self.settings.project_name)
        self.assertEqual(self.service._settings.schedule_time, self.settings.schedule_time)
        self.assertEqual(self.service._settings.platform, self.settings.platform)
        self.assertEqual(self.service._settings.capsule_id, self.settings.capsule_id)
        self.assertEqual(self.service._settings.script, self.settings.script)
        self.assertEqual(self.service._settings.s3_bucket, self.settings.s3_bucket)
        self.assertEqual(self.service._settings.mount, self.settings.mount)
        self.assertEqual(self.service._settings.force_cloud_sync, self.settings.force_cloud_sync)
        self.assertEqual(self.service._settings.transfer_endpoint, self.settings.transfer_endpoint)

    @patch("clabe.data_transfer.aind_watchdog.subprocess.check_output")
    def test_is_running(self, mock_check_output):
        mock_check_output.return_value = (
            "Image Name                     PID Session Name        Session#    Mem Usage\n"
            "========================= ======== ================ =========== ============\n"
            "watchdog.exe                1234 Console                    1    10,000 K\n"
        )
        self.assertTrue(self.service.is_running())

    @patch("clabe.data_transfer.aind_watchdog.subprocess.check_output")
    def test_is_not_running(self, mock_check_output):
        mock_check_output.return_value = "INFO: No tasks are running which match the specified criteria."
        self.assertFalse(self.service.is_running())

    @patch("clabe.data_transfer.aind_watchdog.requests.get")
    def test_get_project_names(self, mock_get):
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.content = '{"data": ["test_project"]}'
        mock_get.return_value = mock_response
        project_names = self.service._get_project_names()
        self.assertIn("test_project", project_names)

    @patch("clabe.data_transfer.aind_watchdog.requests.get")
    def test_get_project_names_fail(self, mock_get):
        mock_response = MagicMock()
        mock_response.ok = False
        mock_get.return_value = mock_response
        with self.assertRaises(Exception):
            self.service._get_project_names()

    @patch(
        "clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running",
        return_value=True,
    )
    @patch(
        "clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_valid_project_name",
        return_value=True,
    )
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._read_yaml")
    def test_validate_success(self, mock_read_yaml, mock_is_valid_project_name, mock_is_running):
        mock_read_yaml.return_value = WatchConfig(
            flag_dir="mock_flag_dir", manifest_complete="manifest_complete_dir"
        ).model_dump()
        with patch.object(Path, "exists", return_value=True):
            self.assertTrue(self.service.validate(create_config=False))

    @patch(
        "clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running",
        return_value=False,
    )
    def test_validate_fail(self, mock_is_running):
        with patch.object(Path, "exists", return_value=False):
            with self.assertRaises(FileNotFoundError):
                self.service.validate()

    def test_missing_env_variables(self):
        del os.environ["WATCHDOG_EXE"]
        del os.environ["WATCHDOG_CONFIG"]
        with self.assertRaises(ValueError):
            WatchdogDataTransferService(
                self.source,
                settings=self.settings,
                validate=self.validate,
            ).with_aind_session_data_mapper(self.aind_data_mapper)

    @patch("clabe.data_transfer.aind_watchdog.Path.exists", return_value=True)
    def test_find_ads_schemas(self, mock_exists):
        source = "mock_source_path"
        expected_files = [Path(source) / f"{file}.json" for file in CORE_FILES]

        result = WatchdogDataTransferService._find_ads_schemas(Path(source))
        self.assertEqual(result, expected_files)

    @patch("clabe.data_transfer.aind_watchdog.Path.mkdir")
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._write_yaml")
    def test_dump_manifest_config(self, mock_write_yaml, mock_mkdir):
        path = Path("flag_dir/manifest_test_manifest.yaml")
        result = self.service.dump_manifest_config()

        self.assertIsInstance(result, Path)
        self.assertIsInstance(path, Path)
        self.assertEqual(result.resolve(), path.resolve())

        mock_write_yaml.assert_called_once()
        mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)

    @patch("clabe.data_transfer.aind_watchdog.Path.mkdir")
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._write_yaml")
    def test_dump_manifest_config_custom_path(self, mock_write_yaml, mock_mkdir):
        custom_path = Path("custom_path/manifest_test_manifest.yaml")
        result = self.service.dump_manifest_config(path=custom_path)

        self.assertIsInstance(result, Path)
        self.assertIsInstance(custom_path, Path)
        self.assertEqual(result.resolve(), custom_path.resolve())
        mock_write_yaml.assert_called_once()
        mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)

    def test_dump_manifest_config_no_manifest_config(self):
        self.service._manifest_config = None

        with self.assertRaises(ValueError):
            self.service.dump_manifest_config()

    def test_dump_manifest_config_no_watch_config(self):
        self.service._watch_config = None

        with self.assertRaises(ValueError):
            self.service.dump_manifest_config()

    def test_add_transfer_service_args_from_factory(self):
        def modality_configs_factory(watchdog_service: WatchdogDataTransferService):
            return ModalityConfigs(
                modality="behavior-videos",
                source=(Path(watchdog_service._source) / "behavior-videos").as_posix(),
                compress_raw_data=True,
                job_settings={"key": "value"},
            )

        _manifest_config = self.service.add_transfer_service_args(
            self.service._manifest_config, jobs=[modality_configs_factory]
        )

        for job in _manifest_config.transfer_service_args.upload_jobs:
            self.assertEqual(job, _manifest_config.transfer_service_args.upload_jobs[-1])

    def test_add_transfer_service_args_from_instance(self):
        modality_configs = ModalityConfigs(
            modality="behavior-videos",
            source=(Path(self.service._source) / "behavior-videos").as_posix(),
            compress_raw_data=True,
            job_settings={"key": "value"},  # needs mode to be json, otherwise parent class will raise an error
        )

        _manifest_config = self.service.add_transfer_service_args(
            self.service._manifest_config, jobs=[modality_configs]
        )

        for job in _manifest_config.transfer_service_args.upload_jobs:
            self.assertEqual(job, _manifest_config.transfer_service_args.upload_jobs[-1])

    def test_add_transfer_service_args_fail_on_duplicate_modality(self):
        def modality_configs_factory(watchdog_service: WatchdogDataTransferService):
            return ModalityConfigs(
                modality="behavior-videos",
                source=(Path(watchdog_service._source) / "behavior-videos").as_posix(),
                compress_raw_data=True,
                job_settings={"key": "value"},
            )

        modality_configs = ModalityConfigs(
            modality="behavior-videos",
            source=(Path(self.service._source) / "behavior-videos").as_posix(),
            job_settings={"key": "value"},  # needs mode to be json, otherwise parent class will raise an error
        )

        with self.assertRaises(ValueError):
            _ = self.service.add_transfer_service_args(
                self.service._manifest_config, jobs=[modality_configs_factory, modality_configs]
            )


class TestRobocopyService(unittest.TestCase):
    def setUp(self):
        self.source = Path("source_path")

        self.settings = RobocopySettings(
            destination=Path("destination_path"),
            log=Path("log_path"),
            extra_args="/MIR",
            delete_src=True,
            overwrite=True,
            force_dir=False,
        )
        self.service = RobocopyService(
            source=self.source,
            settings=self.settings,
            ui_helper=MockUiHelper(),
        )

    def test_initialization(self):
        self.assertEqual(self.service.source, self.source)
        self.assertEqual(self.service._settings.destination, self.settings.destination)
        self.assertEqual(self.service._settings.log, self.settings.log)
        self.assertEqual(self.service._settings.extra_args, self.settings.extra_args)
        self.assertTrue(self.service._settings.delete_src)
        self.assertTrue(self.service._settings.overwrite)
        self.assertFalse(self.service._settings.force_dir)

    @patch("src.clabe.data_transfer.robocopy.subprocess.Popen")
    @patch.object(MockUiHelper, "prompt_yes_no_question", return_value=True)
    def test_transfer(self, mock_ui_helper, mock_popen):
        mock_process = MagicMock()
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process
        self.service.transfer()

    def test_solve_src_dst_mapping_single_path(self):
        result = self.service._solve_src_dst_mapping(self.source, self.settings.destination)
        self.assertEqual(result, {Path(self.source): Path(self.settings.destination)})

    def test_solve_src_dst_mapping_dict(self):
        source_dict = {self.source: self.settings.destination}
        result = self.service._solve_src_dst_mapping(source_dict, None)
        self.assertEqual(result, source_dict)

    def test_solve_src_dst_mapping_invalid(self):
        with self.assertRaises(ValueError):
            self.service._solve_src_dst_mapping(self.source, None)


if __name__ == "__main__":
    unittest.main()
