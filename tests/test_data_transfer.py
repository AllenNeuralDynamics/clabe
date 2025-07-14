import os
import unittest
from datetime import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from aind_data_schema.core.metadata import CORE_FILES
from aind_data_schema_models.modalities import Modality
from aind_data_schema_models.platforms import Platform
from aind_watchdog_service.models.manifest_config import BucketType

from clabe.data_mapper.aind_data_schema import AindDataSchemaSessionDataMapper
from clabe.data_transfer import RobocopyService
from clabe.data_transfer.aind_watchdog import (
    ManifestConfig,
    ModalityConfigs,
    WatchConfig,
    WatchdogDataTransferService,
)

from .fixtures import MockUiHelper


class TestWatchdogDataTransferService(unittest.TestCase):
    def setUp(self):
        os.environ["WATCHDOG_EXE"] = "watchdog.exe"
        os.environ["WATCHDOG_CONFIG"] = "watchdog_config.yml"
        self.source = "source_path"
        self.destination = "destination_path"
        self.aind_data_mapper = MagicMock(spec=AindDataSchemaSessionDataMapper)
        self.schedule_time = time(hour=20)
        self.project_name = "test_project"
        self.platform = Platform.BEHAVIOR
        self.capsule_id = "capsule_id"
        self.script = {"script_key": ["script_value"]}
        self.s3_bucket = BucketType.PRIVATE
        self.mount = "mount_path"
        self.force_cloud_sync = True
        self.transfer_endpoint = "http://aind-data-transfer-service/api/v1/submit_jobs"
        self.validate = False

        self.service = WatchdogDataTransferService(
            self.source,
            destination=self.destination,
            aind_session_data_mapper=self.aind_data_mapper,
            schedule_time=self.schedule_time,
            project_name=self.project_name,
            platform=self.platform,
            capsule_id=self.capsule_id,
            script=self.script,
            s3_bucket=self.s3_bucket,
            mount=self.mount,
            force_cloud_sync=self.force_cloud_sync,
            transfer_endpoint=self.transfer_endpoint,
            validate=self.validate,
            ui_helper=MockUiHelper(),
        )

        self.service._manifest_config = ManifestConfig(
            name="test_manifest",
            modalities={Modality.BEHAVIOR: ["path/to/behavior"], Modality.BEHAVIOR_VIDEOS: ["path/to/behavior-videos"]},
            subject_id=1,
            acquisition_datetime="2023-01-01T00:00:00",
            schemas=["path/to/schema"],
            destination="path/to/destination",
            mount="mount_path",
            processor_full_name="processor_name",
            project_name="test_project",
            schedule_time=self.schedule_time,
            platform=Platform.BEHAVIOR,
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
        self.assertEqual(self.service.destination, self.destination)
        self.assertEqual(self.service.project_name, self.project_name)
        self.assertEqual(self.service.schedule_time, self.schedule_time)
        self.assertEqual(self.service.platform, self.platform)
        self.assertEqual(self.service.capsule_id, self.capsule_id)
        self.assertEqual(self.service.script, self.script)
        self.assertEqual(self.service.s3_bucket, self.s3_bucket)
        self.assertEqual(self.service.mount, self.mount)
        self.assertEqual(self.service.force_cloud_sync, self.force_cloud_sync)
        self.assertEqual(self.service.transfer_endpoint, self.transfer_endpoint)

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
                destination=self.destination,
                aind_session_data_mapper=self.aind_data_mapper,
                schedule_time=self.schedule_time,
                project_name=self.project_name,
                platform=self.platform,
                capsule_id=self.capsule_id,
                script=self.script,
                s3_bucket=self.s3_bucket,
                mount=self.mount,
                force_cloud_sync=self.force_cloud_sync,
                transfer_endpoint=self.transfer_endpoint,
                validate=self.validate,
            )

    @patch("clabe.data_transfer.aind_watchdog.Path.exists", return_value=True)
    def test_find_ads_schemas(self, mock_exists):
        source = "mock_source_path"
        expected_files = [Path(source) / f"{file}.json" for file in CORE_FILES]

        result = WatchdogDataTransferService._find_ads_schemas(source)
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
                modality=Modality.BEHAVIOR_VIDEOS,
                source=(Path(watchdog_service.source) / Modality.BEHAVIOR_VIDEOS.abbreviation).as_posix(),
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
            modality=Modality.BEHAVIOR_VIDEOS,
            source=(Path(self.service.source) / Modality.BEHAVIOR_VIDEOS.abbreviation).as_posix(),
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
                modality=Modality.BEHAVIOR_VIDEOS,
                source=(Path(watchdog_service.source) / Modality.BEHAVIOR_VIDEOS.abbreviation).as_posix(),
                compress_raw_data=True,
                job_settings={"key": "value"},
            )

        modality_configs = ModalityConfigs(
            modality=Modality.BEHAVIOR_VIDEOS,
            source=(Path(self.service.source) / Modality.BEHAVIOR_VIDEOS.abbreviation).as_posix(),
            job_settings={"key": "value"},  # needs mode to be json, otherwise parent class will raise an error
        )

        with self.assertRaises(ValueError):
            _ = self.service.add_transfer_service_args(
                self.service._manifest_config, jobs=[modality_configs_factory, modality_configs]
            )


class TestRobocopyService(unittest.TestCase):
    def setUp(self):
        self.source = "source_path"
        self.destination = "destination_path"
        self.log = "log_path"
        self.extra_args = "/MIR"
        self.service = RobocopyService(
            source=self.source,
            destination=self.destination,
            log=self.log,
            extra_args=self.extra_args,
            delete_src=True,
            overwrite=True,
            force_dir=False,
            ui_helper=MockUiHelper(),
        )

    def test_initialization(self):
        self.assertEqual(self.service.source, self.source)
        self.assertEqual(self.service.destination, self.destination)
        self.assertEqual(self.service.log, self.log)
        self.assertEqual(self.service.extra_args, self.extra_args)
        self.assertTrue(self.service.delete_src)
        self.assertTrue(self.service.overwrite)
        self.assertFalse(self.service.force_dir)

    @patch("src.clabe.data_transfer.robocopy.subprocess.Popen")
    @patch.object(MockUiHelper, "prompt_yes_no_question", return_value=True)
    def test_transfer(self, mock_ui_helper, mock_popen):
        mock_process = MagicMock()
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process
        self.service.transfer()

    def test_solve_src_dst_mapping_single_path(self):
        result = self.service._solve_src_dst_mapping(self.source, self.destination)
        self.assertEqual(result, {Path(self.source): Path(self.destination)})

    def test_solve_src_dst_mapping_dict(self):
        source_dict = {self.source: self.destination}
        result = self.service._solve_src_dst_mapping(source_dict, None)
        self.assertEqual(result, source_dict)

    def test_solve_src_dst_mapping_invalid(self):
        with self.assertRaises(ValueError):
            self.service._solve_src_dst_mapping(self.source, None)


if __name__ == "__main__":
    unittest.main()
