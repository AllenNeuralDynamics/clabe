"""Upload groups: one watchdog manifest per independently uploaded group of modalities."""

import datetime
import warnings
from pathlib import Path
from unittest.mock import patch

import pydantic
import pytest
import yaml
from aind_behavior_services import Session
from aind_data_transfer_service.models.core import Task

from clabe.aind.watchdog import (
    ManifestConfig,
    UploadGroup,
    WatchdogDataTransferService,
    WatchdogSettings,
)
from tests import TESTS_ASSETS

VIDEO_SPLIT = [
    {"name": "primary", "job_type": "async_0", "creates_record": True, "schedule_time": None},
    {"name": "videos", "job_type": "async_1", "modalities": ["behavior-videos"], "schedule_time": "20:00"},
]


@pytest.fixture(autouse=True)
def watchdog_env(monkeypatch):
    monkeypatch.setenv("WATCHDOG_EXE", "watchdog.exe")
    monkeypatch.setenv("WATCHDOG_CONFIG", str(TESTS_ASSETS / "watch_config.yml"))


@pytest.fixture
def session():
    return Session(
        experiment="mock",
        subject="007",
        session_name="mock_session",
        experimenter=["mock_experimenter"],
        date=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )


def _make_session_dir(root: Path, *modalities: str, schemas: tuple[str, ...] = ("session.json",)) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for modality in modalities:
        (root / modality).mkdir()
    for schema in schemas:
        (root / schema).write_text("{}", encoding="utf-8")
    return root


@pytest.fixture
def behavior_dir(tmp_path):
    return _make_session_dir(tmp_path / "rig", "behavior", "behavior-videos", "fib")


def _settings(**kwargs) -> WatchdogSettings:
    """Settings with upload groups, which get a job type unless they set one, or deprecated single-job settings."""
    if "upload_groups" in kwargs:
        kwargs["upload_groups"] = [
            {"job_type": "group_job", **g} if isinstance(g, dict) else g for g in kwargs["upload_groups"]
        ]
    else:
        kwargs.setdefault("job_type", "single_job")
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", "WatchdogSettings .* are deprecated", FutureWarning)
        return WatchdogSettings(destination=Path("//vast/destination"), project_name="test_project", **kwargs)


def _service(source, settings: WatchdogSettings, session: Session) -> WatchdogDataTransferService:
    return WatchdogDataTransferService(
        source, settings=settings, session=session, email_from_experimenter_builder=lambda _: "a@b.org"
    )


def _job(manifest: ManifestConfig):
    assert manifest.transfer_service_args is not None
    (job,) = manifest.transfer_service_args.upload_jobs
    return job


class TestUploadGroupSettings:
    def test_unknown_modality(self):
        with pytest.raises(pydantic.ValidationError, match="Unknown modalities"):
            UploadGroup(name="x", modalities=["not-a-modality"])

    def test_empty_modalities(self):
        with pytest.raises(pydantic.ValidationError, match="must not be empty"):
            UploadGroup(name="x", modalities=[])

    def test_invalid_name(self):
        with pytest.raises(pydantic.ValidationError):
            UploadGroup(name="has spaces")

    @pytest.mark.parametrize(
        "groups, match",
        [
            ([{"name": "a"}, {"name": "b", "modalities": ["fib"]}], "Exactly one upload group must set creates_record"),
            (
                [{"name": "a", "creates_record": True}, {"name": "b", "creates_record": True, "modalities": ["fib"]}],
                "Exactly one upload group must set creates_record",
            ),
            ([{"name": "a", "creates_record": True}, {"name": "a", "modalities": ["fib"]}], "unique"),
            ([{"name": "a", "creates_record": True}, {"name": "b"}], "At most one upload group can claim"),
            (
                [
                    {"name": "a", "creates_record": True, "modalities": ["fib"]},
                    {"name": "b", "modalities": ["fib", "behavior"]},
                ],
                "claimed by both",
            ),
            ([], "must not be empty"),
        ],
    )
    def test_invalid_groups(self, groups, match):
        with pytest.raises(pydantic.ValidationError, match=match):
            _settings(upload_groups=groups)

    def test_single_job_fields_are_one_group(self):
        with pytest.warns(FutureWarning, match="deprecated"):
            settings = WatchdogSettings(
                destination=Path("//vast/destination"),
                project_name="test_project",
                job_type="single_job",
                schedule_time=datetime.time(21),
                delete_modalities_source_after_success=True,
                upload_tasks={"extra": Task(job_settings={"a": 1})},
            )
        settings.job_type = "changed_after_creation"
        (group,) = settings.resolved_upload_groups()
        assert group.creates_record and group.modalities == "*"
        assert group.job_type == "changed_after_creation"
        assert group.schedule_time == datetime.time(21)
        assert group.delete_modalities_source_after_success is True
        assert group.upload_tasks is not None and "extra" in group.upload_tasks

    @pytest.mark.parametrize(
        "field, value",
        [
            ("job_type", "x"),
            ("schedule_time", datetime.time(21)),
            ("delete_modalities_source_after_success", True),
            ("upload_tasks", {}),
        ],
    )
    def test_single_job_fields_with_groups_are_ignored(self, field, value, caplog):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            settings = _settings(upload_groups=VIDEO_SPLIT, **{field: value})
        with caplog.at_level("WARNING"):
            groups = settings.resolved_upload_groups()
        assert [g.name for g in groups] == ["primary", "videos"]
        assert f"'{field}'" in caplog.text and "ignored because upload_groups is set" in caplog.text

    def test_single_job_field_set_after_creation_with_groups_is_ignored(self, caplog):
        """The olfactometer calibration pattern: overriding job_type in code while clabe.yml has groups."""
        settings = _settings(upload_groups=VIDEO_SPLIT)
        settings.job_type = "upload_only_v2"
        with caplog.at_level("WARNING"):
            record, _ = settings.resolved_upload_groups()
        assert record.job_type == "async_0"
        assert "'job_type'" in caplog.text

    def test_defaults_do_not_warn(self, caplog):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            settings = WatchdogSettings(destination=Path("//vast/destination"), project_name="test_project")
        with caplog.at_level("WARNING"):
            (group,) = settings.resolved_upload_groups()
        assert group.job_type == "default" and not caplog.text

    def test_group_schedule_defaults_to_20h_and_null_is_now(self):
        record, _ = _settings(upload_groups=VIDEO_SPLIT).resolved_upload_groups()
        assert record.schedule_time is None
        assert UploadGroup(name="x", job_type="j").schedule_time == datetime.time(20)

    def test_group_requires_job_type(self):
        with pytest.raises(pydantic.ValidationError, match="job_type"):
            UploadGroup(name="x")

    def test_record_group_is_first(self):
        settings = _settings(
            upload_groups=[{"name": "v", "modalities": ["fib"]}, {"name": "primary", "creates_record": True}]
        )
        assert [g.name for g in settings.resolved_upload_groups()] == ["primary", "v"]

    def test_from_yaml_section(self, tmp_path):
        """Groups parse from clabe.yml the way an experiment repository writes them."""
        text = """
upload_groups:
  - name: primary
    job_type: async_0
    creates_record: true
    schedule_time: null
  - name: videos
    job_type: async_1
    modalities: [behavior-videos]
    schedule_time: "20:00"
"""
        groups = [UploadGroup.model_validate(g) for g in yaml.safe_load(text)["upload_groups"]]
        record, videos = _settings(upload_groups=groups).resolved_upload_groups()
        assert record.schedule_time is None and videos.schedule_time == datetime.time(20)


class TestPlan:
    def test_single_group_by_default(self, behavior_dir, session):
        (planned,) = _service(behavior_dir, _settings(), session).plan()
        assert planned.filename == "manifest_mock_session.yaml"
        assert planned.creates_record
        assert set(planned.manifest.modalities) == {"behavior", "behavior-videos", "fib"}
        assert "gather_preliminary_metadata" in _job(planned.manifest).tasks
        assert _job(planned.manifest).job_type == "single_job"

    def test_project_name_reaches_manifest_and_job(self, behavior_dir, session):
        (planned,) = _service(behavior_dir, _settings(), session).plan()
        assert planned.manifest.project_name == "test_project"
        assert _job(planned.manifest).project_name == "test_project"

    def test_project_name_reaches_every_group(self, behavior_dir, session):
        settings = _settings(upload_groups=VIDEO_SPLIT)
        for planned in _service(behavior_dir, settings, session).plan():
            assert planned.manifest.project_name == "test_project"
            assert _job(planned.manifest).project_name == "test_project"

    def test_without_project_name(self, behavior_dir, session):
        settings = WatchdogSettings(destination=Path("//vast/destination"))
        (planned,) = _service(behavior_dir, settings, session).plan()
        assert planned.manifest.project_name is None
        assert _job(planned.manifest).project_name is None

    def test_video_split(self, behavior_dir, session):
        settings = _settings(upload_groups=VIDEO_SPLIT, data_description_tags=["tag"])
        primary, videos = _service(behavior_dir, settings, session).plan()

        assert primary.filename == "manifest_mock_session.yaml"
        assert set(primary.manifest.modalities) == {"behavior", "fib"}
        assert primary.manifest.schemas and primary.manifest.schedule_time is None
        primary_job = _job(primary.manifest)
        assert primary_job.job_type == "async_0"
        gather = primary_job.tasks["gather_preliminary_metadata"]
        assert isinstance(gather, Task) and gather.job_settings is not None
        assert gather.job_settings["data_description_settings"]["tags"] == ["tag"]

        assert videos.filename == "manifest_mock_session__videos.yaml"
        assert set(videos.manifest.modalities) == {"behavior-videos"}
        assert videos.manifest.schemas == []
        assert videos.manifest.schedule_time == datetime.time(20)
        videos_job = _job(videos.manifest)
        assert videos_job.job_type == "async_1"
        assert videos.manifest.transfer_service_job_type == "async_1"
        assert "gather_preliminary_metadata" not in videos_job.tasks
        assert set(videos_job.tasks["modality_transformation_settings"]) == {"behavior-videos"}

        # Both land in the same folder, so they are one asset
        assert primary.manifest.name == videos.manifest.name
        assert primary.manifest.destination == videos.manifest.destination

    def test_group_without_its_modalities_is_skipped(self, tmp_path, session, caplog):
        source = _make_session_dir(tmp_path / "rig", "behavior")
        with caplog.at_level("WARNING"):
            planned = _service(source, _settings(upload_groups=VIDEO_SPLIT), session).plan()
        assert [p.group for p in planned] == ["primary"]
        assert "found none of its modalities" in caplog.text

    def test_group_missing_some_of_its_modalities_logs_info(self, tmp_path, session, caplog):
        source = _make_session_dir(tmp_path / "rig", "behavior", "behavior-videos")
        groups = [VIDEO_SPLIT[0], {**VIDEO_SPLIT[1], "modalities": ["behavior-videos", "fib"]}]
        with caplog.at_level("INFO"):
            _, videos = _service(source, _settings(upload_groups=groups), session).plan()
        assert list(videos.manifest.modalities) == ["behavior-videos"]
        (record,) = [r for r in caplog.records if "did not find" in r.getMessage()]
        assert record.levelname == "INFO" and "['fib']" in record.getMessage()

    def test_record_group_without_modalities_raises(self, tmp_path, session):
        source = _make_session_dir(tmp_path / "rig", "behavior-videos")
        with pytest.raises(ValueError, match="creates the record but found none"):
            _service(source, _settings(upload_groups=VIDEO_SPLIT), session).plan()

    def test_unclaimed_modality_raises(self, behavior_dir, session):
        groups = [
            {"name": "primary", "creates_record": True, "modalities": ["behavior"]},
            {"name": "videos", "modalities": ["behavior-videos"]},
        ]
        with pytest.raises(ValueError, match=r"\['fib'\] are claimed by no upload group"):
            _service(behavior_dir, _settings(upload_groups=groups), session).plan()

    def test_appending_group_submitted_before_record_raises(self, behavior_dir, session):
        groups = [
            {"name": "primary", "creates_record": True, "schedule_time": "20:00"},
            {"name": "videos", "modalities": ["behavior-videos"], "schedule_time": None},
        ]
        with pytest.raises(ValueError, match="is submitted now, before 'primary'"):
            _service(behavior_dir, _settings(upload_groups=groups), session).plan()

    def test_appending_group_at_earlier_time_warns(self, behavior_dir, session, caplog):
        groups = [
            {"name": "primary", "creates_record": True, "schedule_time": "20:00"},
            {"name": "videos", "modalities": ["behavior-videos"], "schedule_time": "02:00"},
        ]
        with caplog.at_level("WARNING"):
            assert len(_service(behavior_dir, _settings(upload_groups=groups), session).plan()) == 2
        assert "unless it runs after midnight" in caplog.text

    def test_per_group_upload_tasks_interpolate_destination(self, behavior_dir, session):
        groups = [
            {"name": "primary", "creates_record": True, "schedule_time": None},
            {
                "name": "videos",
                "modalities": ["behavior-videos"],
                "upload_tasks": {"compress": {"job_settings": {"input": "{{ destination }}/behavior-videos"}}},
            },
        ]
        primary, videos = _service(behavior_dir, _settings(upload_groups=groups), session).plan()
        assert "compress" not in _job(primary.manifest).tasks
        task = _job(videos.manifest).tasks["compress"]
        assert isinstance(task, Task) and task.job_settings is not None
        # The destination is resolved, which differs between Windows and POSIX for a UNC path
        root = WatchdogDataTransferService._remote_destination_root(videos.manifest)
        assert Path(task.job_settings["input"]) == root / "behavior-videos"
        assert root.name == "mock_session"


class TestSecondMachine:
    """A folder recorded on another machine, read by the launcher's watchdog through its share."""

    @pytest.fixture
    def ephys_share(self, tmp_path):
        return _make_session_dir(tmp_path / "share", "ecephys", schemas=("ecephys_acquisition.json",))

    def test_own_group_for_the_other_machine(self, behavior_dir, ephys_share, session):
        groups = [
            {"name": "primary", "creates_record": True, "schedule_time": None},
            {"name": "ecephys", "job_type": "ephys_append", "modalities": ["ecephys"]},
        ]
        primary, ecephys = _service([behavior_dir, ephys_share], _settings(upload_groups=groups), session).plan()

        assert "ecephys" not in primary.manifest.modalities
        # The record gets the metadata files of every source, the other machine's through its share
        assert any(s.endswith("share/ecephys_acquisition.json") for s in primary.manifest.schemas)
        assert ecephys.manifest.modalities == {"ecephys": [(ephys_share / "ecephys").resolve().as_posix()]}
        assert ecephys.manifest.schemas == []
        assert _job(ecephys.manifest).job_type == "ephys_append"


@patch("clabe.aind.watchdog.WatchdogDataTransferService.is_running", return_value=True)
class TestTransfer:
    def test_writes_one_manifest_per_group(self, _, behavior_dir, session, tmp_path):
        service = _service(behavior_dir, _settings(upload_groups=VIDEO_SPLIT), session)
        assert service._watch_config is not None
        service._watch_config.flag_dir = str(tmp_path / "flags")
        service.transfer()
        written = sorted(p.name for p in (tmp_path / "flags").iterdir())
        assert written == ["manifest_mock_session.yaml", "manifest_mock_session__videos.yaml"]
        videos = yaml.safe_load((tmp_path / "flags" / "manifest_mock_session__videos.yaml").read_text())
        assert list(videos["modalities"]) == ["behavior-videos"]
        assert service._manifest_config is not None
        assert set(service._manifest_config.modalities) == {"behavior", "fib"}

    def test_record_manifest_is_written_first(self, _, behavior_dir, session, tmp_path):
        service = _service(behavior_dir, _settings(upload_groups=VIDEO_SPLIT), session)
        with patch.object(service, "dump_manifest_config", return_value=tmp_path / "m.yaml") as dump:
            service.transfer()
        assert [Path(c.kwargs["path"]).name for c in dump.call_args_list] == [
            "manifest_mock_session.yaml",
            "manifest_mock_session__videos.yaml",
        ]

    def test_failed_record_manifest_writes_nothing_else(self, _, behavior_dir, session):
        service = _service(behavior_dir, _settings(upload_groups=VIDEO_SPLIT), session)
        with (
            patch.object(service, "dump_manifest_config", side_effect=OSError("disk full")) as dump,
            pytest.raises(OSError, match="disk full"),
        ):
            service.transfer()
        dump.assert_called_once()

    def test_not_running_logs_every_manifest(self, mock_is_running, behavior_dir, session, tmp_path, caplog):
        mock_is_running.return_value = False
        service = _service(behavior_dir, _settings(upload_groups=VIDEO_SPLIT), session)
        assert service._watch_config is not None
        service._watch_config.flag_dir = str(tmp_path / "flags")
        with (
            patch.object(WatchdogDataTransferService, "force_restart", return_value=None),
            caplog.at_level("ERROR"),
        ):
            service.transfer()
        assert "manifest_mock_session.yaml" in caplog.text
        assert "manifest_mock_session__videos.yaml" in caplog.text


def test_project_name_is_optional():
    settings = WatchdogSettings(destination=Path("//vast/destination"))
    assert settings.project_name is None
