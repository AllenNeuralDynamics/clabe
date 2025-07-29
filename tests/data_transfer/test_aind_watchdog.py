import os
import subprocess
import unittest
from datetime import datetime, time
from pathlib import Path
from unittest.mock import MagicMock, patch

from aind_data_schema.core.metadata import CORE_FILES
from aind_data_schema.core.session import Session as AdsSession
from aind_watchdog_service.models.manifest_config import BucketType, ManifestConfig, ModalityConfigs, Platform
from aind_watchdog_service.models.watch_config import WatchConfig
from requests.exceptions import HTTPError

from clabe.data_mapper.aind_data_schema import AindDataSchemaSessionDataMapper
from clabe.data_transfer.aind_watchdog import (
    WatchdogDataTransferService,
    WatchdogSettings,
)
from clabe.launcher import BaseLauncher

from ..fixtures import MockUiHelper


class TestWatchdogDataTransferService(unittest.TestCase):
    def setUp(self):
        os.environ["WATCHDOG_EXE"] = "watchdog.exe"
        os.environ["WATCHDOG_CONFIG"] = "watchdog_config.yml"
        self.source = Path("source_path")
        self.validate = False
        self.aind_data_mapper = MagicMock(spec=AindDataSchemaSessionDataMapper)
        self.aind_data_mapper.is_mapped.return_value = True
        self.aind_data_mapper.mapped = MagicMock(spec=AdsSession)
        self.aind_data_mapper.mapped.experimenter_full_name = ["John Doe"]
        self.aind_data_mapper.mapped.subject_id = "12345"
        self.aind_data_mapper.mapped.session_start_time = datetime(2023, 1, 1, 10, 0, 0)
        self.aind_data_mapper.mapped.data_streams = [MagicMock()]
        self.aind_data_mapper.mapped.data_streams[0].stream_modalities = [MagicMock(abbreviation="behavior")]

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

    def tearDown(self):
        if "WATCHDOG_EXE" in os.environ:
            del os.environ["WATCHDOG_EXE"]
        if "WATCHDOG_CONFIG" in os.environ:
            del os.environ["WATCHDOG_CONFIG"]

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
        self.assertEqual(self.service.executable_path, Path("watchdog.exe"))
        self.assertEqual(self.service.config_path, Path("watchdog_config.yml"))

    def test_missing_env_variables(self):
        del os.environ["WATCHDOG_EXE"]
        del os.environ["WATCHDOG_CONFIG"]
        with self.assertRaises(ValueError):
            WatchdogDataTransferService(
                self.source,
                settings=self.settings,
                validate=self.validate,
            )

    def test_aind_session_data_mapper_get(self):
        self.service.with_aind_session_data_mapper(self.aind_data_mapper)
        self.assertEqual(self.service.aind_session_data_mapper, self.aind_data_mapper)

    def test_aind_session_data_mapper_get_not_set(self):
        self.service._aind_session_data_mapper = None
        with self.assertRaises(ValueError):
            _ = self.service.aind_session_data_mapper

    def test_with_aind_session_data_mapper(self):
        returned_service = self.service.with_aind_session_data_mapper(self.aind_data_mapper)
        self.assertEqual(self.service._aind_session_data_mapper, self.aind_data_mapper)
        self.assertEqual(returned_service, self.service)

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running", return_value=False)
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.force_restart", return_value=None)
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.dump_manifest_config")
    def test_transfer_service_not_running_restart_success(
        self, mock_dump_manifest_config, mock_force_restart, mock_is_running
    ):
        mock_is_running.side_effect = [False, True]  # First call returns False, second returns True
        self.service.with_aind_session_data_mapper(self.aind_data_mapper)
        self.service.transfer()
        mock_force_restart.assert_called_once_with(kill_if_running=False)
        mock_dump_manifest_config.assert_called_once()

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running", return_value=False)
    @patch(
        "clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.force_restart",
        side_effect=subprocess.CalledProcessError(1, "cmd"),
    )
    def test_transfer_service_not_running_restart_fail(self, mock_force_restart, mock_is_running):
        self.service.with_aind_session_data_mapper(self.aind_data_mapper)
        with self.assertRaises(RuntimeError):
            self.service.transfer()
        mock_force_restart.assert_called_once_with(kill_if_running=False)

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running", return_value=True)
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.dump_manifest_config")
    def test_transfer_data_mapper_not_mapped(self, mock_dump_manifest_config, mock_is_running):
        self.aind_data_mapper.is_mapped.return_value = False
        self.service.with_aind_session_data_mapper(self.aind_data_mapper)
        with self.assertRaises(ValueError):
            self.service.transfer()
        mock_dump_manifest_config.assert_not_called()

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running", return_value=True)
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.dump_manifest_config")
    def test_transfer_watch_config_none(self, mock_dump_manifest_config, mock_is_running):
        self.service._watch_config = None
        self.service.with_aind_session_data_mapper(self.aind_data_mapper)
        with self.assertRaises(ValueError):
            self.service.transfer()
        mock_dump_manifest_config.assert_not_called()

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running", return_value=True)
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.dump_manifest_config")
    def test_transfer_success(self, mock_dump_manifest_config, mock_is_running):
        self.service.with_aind_session_data_mapper(self.aind_data_mapper)
        self.service.transfer()
        mock_dump_manifest_config.assert_called_once()

    @patch("clabe.data_transfer.aind_watchdog.Path.exists", return_value=False)
    def test_validate_executable_not_found(self, mock_exists):
        with self.assertRaises(FileNotFoundError):
            self.service.validate()

    @patch("clabe.data_transfer.aind_watchdog.Path.exists")
    def test_validate_config_not_found_no_create(self, mock_exists):
        mock_exists.side_effect = [True, False]  # executable exists, config does not
        with self.assertRaises(FileNotFoundError):
            self.service.validate(create_config=False)

    @patch("clabe.data_transfer.aind_watchdog.Path.exists")
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._write_yaml")
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.create_watch_config")
    def test_validate_config_not_found_create(
        self, mock_create_watch_config, mock_write_yaml, mock_exists
    ):
        mock_exists.side_effect = [True, False]  # executable exists, config does not
        mock_create_watch_config.return_value = MagicMock(spec=WatchConfig)
        self.service.validate(create_config=True)
        mock_create_watch_config.assert_called_once()
        mock_write_yaml.assert_called_once()

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running", return_value=False)
    @patch("clabe.data_transfer.aind_watchdog.Path.exists", return_value=True)
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._read_yaml", return_value={"flag_dir": "mock_flag_dir", "manifest_complete": "mock_manifest_complete"})
    def test_validate_service_not_running(self, mock_exists, mock_is_running, mock_read_yaml):
        self.assertFalse(self.service.validate())

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_valid_project_name", return_value=False)
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running", return_value=True)
    @patch("clabe.data_transfer.aind_watchdog.Path.exists", return_value=True)
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._read_yaml", return_value={"flag_dir": "mock_flag_dir", "manifest_complete": "mock_manifest_complete"})
    def test_validate_invalid_project_name(self, mock_read_yaml, mock_exists, mock_is_running, mock_is_valid_project_name):
        self.assertFalse(self.service.validate())

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_valid_project_name", side_effect=HTTPError)
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running", return_value=True)
    @patch("clabe.data_transfer.aind_watchdog.Path.exists", return_value=True)
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._read_yaml", return_value={})
    def test_validate_http_error(self, mock_read_yaml, mock_exists, mock_is_running, mock_is_valid_project_name):
        with self.assertRaises(HTTPError):
            self.service.validate()

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_valid_project_name", return_value=True)
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running", return_value=True)
    @patch("clabe.data_transfer.aind_watchdog.Path.exists", return_value=True)
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._read_yaml", return_value={"flag_dir": "mock_flag_dir", "manifest_complete": "mock_manifest_complete"})
    def test_validate_success(self, mock_read_yaml, mock_exists, mock_is_running, mock_is_valid_project_name):
        self.assertTrue(self.service.validate())

    @patch("clabe.data_transfer.aind_watchdog.Path.mkdir")
    @patch("clabe.data_transfer.aind_watchdog.Path.exists")
    def test_create_watch_config_create_dir(self, mock_exists, mock_mkdir):
        mock_exists.side_effect = [False, False]
        watched_dir = Path("test_watched_dir")
        manifest_complete_dir = Path("test_manifest_complete_dir")
        config = WatchdogDataTransferService.create_watch_config(watched_dir, manifest_complete_dir, create_dir=True)
        self.assertIsInstance(config, WatchConfig)
        mock_mkdir.assert_called_with(parents=True, exist_ok=True)
        self.assertEqual(mock_mkdir.call_count, 2)

    @patch("clabe.data_transfer.aind_watchdog.Path.mkdir")
    @patch("clabe.data_transfer.aind_watchdog.Path.exists", return_value=False)
    def test_create_watch_config_no_create_dir(self, mock_exists, mock_mkdir):
        watched_dir = Path("test_watched_dir")
        manifest_complete_dir = Path("test_manifest_complete_dir")
        config = WatchdogDataTransferService.create_watch_config(watched_dir, manifest_complete_dir, create_dir=False)
        self.assertIsInstance(config, WatchConfig)
        mock_mkdir.assert_not_called()

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._get_project_names", return_value=["test_project"])
    def test_is_valid_project_name_valid(self, mock_get_project_names):
        self.assertTrue(self.service.is_valid_project_name())

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._get_project_names", return_value=["other_project"])
    def test_is_valid_project_name_invalid(self, mock_get_project_names):
        self.assertFalse(self.service.is_valid_project_name())

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._get_project_names", return_value=["other_project"])
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._find_ads_schemas", return_value=[])
    def test_create_manifest_config_from_ads_session_invalid_project_name(self, mock_find_ads_schemas, mock_get_project_names):
        self.service._validate_project_name = True
        with self.assertRaises(ValueError):
            self.service.create_manifest_config_from_ads_session(self.aind_data_mapper.mapped)

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._find_ads_schemas", return_value=[Path("schema1.json")])
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.add_transfer_service_args")
    def test_create_manifest_config_from_ads_session_with_ads_schemas(self, mock_find_ads_schemas, mock_add_transfer_service_args):
        self.service._validate_project_name = False
        manifest_config = self.service.create_manifest_config_from_ads_session(
            self.aind_data_mapper.mapped, ads_schemas=[Path("custom_schema.json")]
        )
        self.assertIsInstance(manifest_config, ManifestConfig)
        self.assertIn("custom_schema.json", [str(s) for s in manifest_config.schemas])
        mock_find_ads_schemas.assert_not_called()
        mock_add_transfer_service_args.assert_called_once()

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.add_transfer_service_args")
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._find_ads_schemas", return_value=[Path("schema1.json")])
    def test_create_manifest_config_from_ads_session_no_ads_schemas(self, mock_add_transfer_service_args, mock_find_ads_schemas):
        mock_add_transfer_service_args.return_value = self.service._manifest_config # Return a ManifestConfig instance
        self.service._validate_project_name = False
        manifest_config = self.service.create_manifest_config_from_ads_session(self.aind_data_mapper.mapped)
        self.assertIsInstance(manifest_config, ManifestConfig)
        mock_find_ads_schemas.assert_called_once()
        self.assertIn("schema1.json", [str(s) for s in manifest_config.schemas])
        mock_add_transfer_service_args.assert_called_once()

    @patch("clabe.data_transfer.aind_watchdog.aind_watchdog_service.models.make_standard_transfer_args")
    def test_add_transfer_service_args_no_jobs(self, mock_make_standard_transfer_args):
        mock_modality_config = MagicMock(spec=ModalityConfigs)
        mock_modality_config.modality = "behavior-videos"
        mock_transfer_service_args = MagicMock()
        mock_transfer_service_args.upload_jobs = [MagicMock()]
        mock_transfer_service_args.upload_jobs[0].modalities = [mock_modality_config]
        mock_make_standard_transfer_args.return_value = mock_transfer_service_args
        manifest_config = self.service.add_transfer_service_args(self.service._manifest_config, jobs=None)
        self.assertIsInstance(manifest_config, ManifestConfig)
        mock_make_standard_transfer_args.assert_called_once()

    @patch("clabe.data_transfer.aind_watchdog.aind_watchdog_service.models.make_standard_transfer_args")
    def test_add_transfer_service_args_with_callable_jobs(self, mock_make_standard_transfer_args):
        mock_modality_config = MagicMock(spec=ModalityConfigs)
        mock_modality_config.modality = "behavior-videos"
        mock_transfer_service_args = MagicMock()
        mock_transfer_service_args.upload_jobs = [MagicMock()]
        mock_transfer_service_args.upload_jobs[0].modalities = [mock_modality_config]
        mock_make_standard_transfer_args.return_value = mock_transfer_service_args

        def modality_configs_factory(watchdog_service: WatchdogDataTransferService):
            return ModalityConfigs(
                modality="behavior-videos",
                source=(Path(watchdog_service._source) / "behavior-videos").as_posix(),
                compress_raw_data=True,
                job_settings={"key": "value"},
            )
        manifest_config = self.service.add_transfer_service_args(self.service._manifest_config, jobs=[modality_configs_factory])
        self.assertIsInstance(manifest_config, ManifestConfig)
        mock_make_standard_transfer_args.assert_called_once()
        self.assertEqual(len(manifest_config.transfer_service_args.upload_jobs[0].modalities), 1)
        self.assertEqual(manifest_config.transfer_service_args.upload_jobs[0].modalities[0].modality, "behavior-videos")

    @patch("clabe.data_transfer.aind_watchdog.aind_watchdog_service.models.make_standard_transfer_args")
    def test_add_transfer_service_args_with_instance_jobs(self, mock_make_standard_transfer_args):
        mock_transfer_service_args = MagicMock()
        mock_transfer_service_args.upload_jobs = [MagicMock()]
        mock_transfer_service_args.upload_jobs[0].modalities = [MagicMock(spec=ModalityConfigs, modality="behavior-videos")]
        mock_make_standard_transfer_args.return_value = mock_transfer_service_args

        modality_configs = ModalityConfigs(
            modality="behavior-videos",
            source=(Path(self.service._source) / "behavior-videos").as_posix(),
            job_settings={"key": "value"},
        )
        manifest_config = self.service.add_transfer_service_args(self.service._manifest_config, jobs=[modality_configs])
        self.assertIsInstance(manifest_config, ManifestConfig)
        mock_make_standard_transfer_args.assert_called_once()
        self.assertEqual(len(manifest_config.transfer_service_args.upload_jobs[0].modalities), 1)
        self.assertEqual(manifest_config.transfer_service_args.upload_jobs[0].modalities[0].modality, "behavior-videos")

    @patch("clabe.data_transfer.aind_watchdog.aind_watchdog_service.models.make_standard_transfer_args")
    def test_add_transfer_service_args_with_submit_job_request_kwargs(self, mock_make_standard_transfer_args):
        mock_transfer_service_args = MagicMock()
        mock_transfer_service_args.model_copy.return_value = mock_transfer_service_args
        mock_make_standard_transfer_args.return_value = mock_transfer_service_args
        
        submit_kwargs = {"some_key": "some_value"}
        manifest_config = self.service.add_transfer_service_args(self.service._manifest_config, submit_job_request_kwargs=submit_kwargs)
        
        mock_make_standard_transfer_args.assert_called_once()
        mock_transfer_service_args.model_copy.assert_called_once_with(update=submit_kwargs)
        self.assertIsInstance(manifest_config, ManifestConfig)

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
            job_settings={"key": "value"},
        )

        with self.assertRaises(ValueError):
            _ = self.service.add_transfer_service_args(
                self.service._manifest_config, jobs=[modality_configs_factory, modality_configs]
            )

    @patch("clabe.data_transfer.aind_watchdog.Path.exists")
    def test_find_ads_schemas_with_existing_schemas(self, mock_exists):
        mock_exists.side_effect = [True, False, True] # rig.json exists, processing.json doesn't, session.json exists
        source_path = Path("mock_source")
        expected_schemas = [source_path / f"{file}.json" for file in ["rig", "session"]]
        result = WatchdogDataTransferService._find_ads_schemas(source_path)
        self.assertEqual(len(result), 2)
        self.assertIn(source_path / "rig.json", result)
        self.assertIn(source_path / "session.json", result)

    @patch("clabe.data_transfer.aind_watchdog.Path.exists", return_value=False)
    def test_find_ads_schemas_no_existing_schemas(self, mock_exists):
        source_path = Path("mock_source")
        result = WatchdogDataTransferService._find_ads_schemas(source_path)
        self.assertEqual(result, [])

    @patch("clabe.data_transfer.aind_watchdog.subprocess.check_output")
    def test_is_running(self, mock_check_output):
        mock_check_output.return_value = "Image Name                     PID Session Name        Session#    Mem Usage\n========================= ======== ================ =========== =============\nwatchdog.exe                1234 Console                    1    10,000 K\n"
        self.assertTrue(self.service.is_running())

    @patch("clabe.data_transfer.aind_watchdog.subprocess.check_output")
    def test_is_not_running(self, mock_check_output):
        mock_check_output.return_value = "INFO: No tasks are running which match the specified criteria."
        self.assertFalse(self.service.is_running())

    @patch("clabe.data_transfer.aind_watchdog.subprocess.run")
    @patch("clabe.data_transfer.aind_watchdog.subprocess.Popen")
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running")
    def test_force_restart_kill_if_running(self, mock_is_running, mock_popen, mock_run):
        mock_is_running.side_effect = [True, False] # First call returns True, second returns False
        self.service.force_restart(kill_if_running=True)
        mock_run.assert_called_once_with(["taskkill", "/IM", self.service.executable_path.name, "/F"], shell=True, check=True)
        mock_popen.assert_called_once()

    @patch("clabe.data_transfer.aind_watchdog.subprocess.run")
    @patch("clabe.data_transfer.aind_watchdog.subprocess.Popen")
    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.is_running", return_value=False)
    def test_force_restart_no_kill(self, mock_is_running, mock_popen, mock_run):
        self.service.force_restart(kill_if_running=False)
        mock_run.assert_not_called()
        mock_popen.assert_called_once()

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._write_yaml")
    @patch("clabe.data_transfer.aind_watchdog.Path.mkdir")
    def test_dump_manifest_config_custom_path(self, mock_mkdir, mock_write_yaml):
        custom_path = Path("custom_path/manifest_test_manifest.yaml")
        result = self.service.dump_manifest_config(path=custom_path)
        self.assertEqual(result.resolve(), custom_path.resolve())
        mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)
        mock_write_yaml.assert_called_once()

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._write_yaml")
    @patch("clabe.data_transfer.aind_watchdog.Path.mkdir")
    def test_dump_manifest_config_default_path(self, mock_mkdir, mock_write_yaml):
        self.service._watch_config = WatchConfig(flag_dir="flag_dir", manifest_complete="manifest_complete")
        self.service._manifest_config.name = "test_manifest"
        result = self.service.dump_manifest_config()
        expected_path = Path("flag_dir/manifest_test_manifest.yaml").resolve()
        self.assertEqual(result.resolve(), expected_path)
        mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)
        mock_write_yaml.assert_called_once()

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService._write_yaml")
    @patch("clabe.data_transfer.aind_watchdog.Path.mkdir")
    def test_dump_manifest_config_prefix_logic(self, mock_mkdir, mock_write_yaml):
        self.service._watch_config = WatchConfig(flag_dir="flag_dir", manifest_complete="manifest_complete")
        self.service._manifest_config.name = "test_manifest"
        custom_path = Path("custom_path/my_manifest.yaml")
        result = self.service.dump_manifest_config(path=custom_path)
        expected_path = Path("custom_path/manifest_my_manifest.yaml").resolve()
        self.assertEqual(result.resolve(), expected_path)
        mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)
        mock_write_yaml.assert_called_once()

    def test_dump_manifest_config_no_manifest_config(self):
        self.service._manifest_config = None
        with self.assertRaises(ValueError):
            self.service.dump_manifest_config()

    def test_dump_manifest_config_no_watch_config(self):
        self.service._watch_config = None
        with self.assertRaises(ValueError):
            self.service.dump_manifest_config()

    @patch("clabe.data_transfer.aind_watchdog.ui.DefaultUIHelper.prompt_yes_no_question", return_value=True)
    def test_prompt_input_yes(self, mock_prompt):
        self.assertTrue(self.service.prompt_input())
        mock_prompt.assert_called_once_with("Would you like to generate a watchdog manifest (Y/N)?")

    @patch("clabe.data_transfer.aind_watchdog.ui.DefaultUIHelper")
    def test_prompt_input_no(self, MockUiHelper):
        MockUiHelper.return_value.prompt_yes_no_question.return_value = False
        self.assertFalse(self.service.prompt_input())
        MockUiHelper.return_value.prompt_yes_no_question.assert_called_once_with("Would you like to generate a watchdog manifest (Y/N)?")

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.transfer")
    @patch("clabe.data_transfer.aind_watchdog.BaseLauncher")
    def test_build_runner_callable_aind_session_data_mapper(self, MockBaseLauncher, mock_transfer):
        mock_launcher = MockBaseLauncher()
        mock_launcher.get_session.return_value = MagicMock(subject="test_subject", session_name="test_session")
        mock_launcher.session_directory = Path("launcher_session_dir")

        def mock_aind_mapper_factory():
            mapper = MagicMock(spec=AindDataSchemaSessionDataMapper)
            mapper.is_mapped.return_value = True
            return mapper

        runner = WatchdogDataTransferService.build_runner(self.settings, mock_aind_mapper_factory)
        service = runner(mock_launcher)

        self.assertIsInstance(service, WatchdogDataTransferService)
        mock_transfer.assert_called_once()
        self.assertEqual(service._settings.destination, Path("destination_path") / "test_subject")
        self.assertEqual(service._source, Path("launcher_session_dir"))
        self.assertEqual(service._session_name, "test_session")

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.transfer")
    @patch("clabe.data_transfer.aind_watchdog.BaseLauncher")
    def test_build_runner_instance_aind_session_data_mapper(self, MockBaseLauncher, mock_transfer):
        mock_launcher = MockBaseLauncher()
        mock_launcher.get_session.return_value = MagicMock(subject="test_subject", session_name="test_session")
        mock_launcher.session_directory = Path("launcher_session_dir")

        mock_aind_mapper_instance = MagicMock(spec=AindDataSchemaSessionDataMapper)
        mock_aind_mapper_instance.is_mapped.return_value = True

        runner = WatchdogDataTransferService.build_runner(self.settings, mock_aind_mapper_instance)
        service = runner(mock_launcher)

        self.assertIsInstance(service, WatchdogDataTransferService)
        mock_transfer.assert_called_once()
        self.assertEqual(service._settings.destination, Path("destination_path") / "test_subject")
        self.assertEqual(service._source, Path("launcher_session_dir"))
        self.assertEqual(service._session_name, "test_session")

    @patch("clabe.data_transfer.aind_watchdog.WatchdogDataTransferService.transfer")
    @patch("clabe.data_transfer.aind_watchdog.BaseLauncher")
    def test_build_runner_data_mapper_not_mapped(self, MockBaseLauncher, mock_transfer):
        mock_launcher = MockBaseLauncher()
        mock_launcher.get_session.return_value = MagicMock(subject="test_subject", session_name="test_session")
        mock_launcher.session_directory = Path("launcher_session_dir")

        mock_aind_mapper_instance = MagicMock(spec=AindDataSchemaSessionDataMapper)
        mock_aind_mapper_instance.is_mapped.return_value = False

        runner = WatchdogDataTransferService.build_runner(self.settings, mock_aind_mapper_instance)
        with self.assertRaises(ValueError):
            runner(mock_launcher)
        mock_transfer.assert_not_called()

if __name__ == "__main__":
    unittest.main()
