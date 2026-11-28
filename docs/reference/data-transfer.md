# Data transfer

[`WatchdogDataTransferService`][clabe.aind.watchdog.WatchdogDataTransferService] hands a session to
[aind-watchdog-service](https://github.com/AllenNeuralDynamics/aind-watchdog-service), which copies it to
`destination` and submits the upload job at `schedule_time`.

- Needs the `WATCHDOG_EXE` and `WATCHDOG_CONFIG` environment variables.
- Settings come from the `watchdog` section of `clabe.yml`, or from code.

## Upload groups

Split a session into several jobs that upload to the same asset, so slow modalities (e.g. videos) do not delay the
others.

```yaml
watchdog:
  destination: '\\allen\aind\scratch\vr-foraging\data'
  project_name: Cognitive flexibility in patch foraging
  upload_groups:
    - name: primary
      job_type: vr_foraging_async_videos_0
      creates_record: true
      schedule_time: null            # submit now
    - name: videos
      job_type: vr_foraging_async_videos_1
      modalities: [behavior-videos]
      delete_modalities_source_after_success: true
```

Or in code:

```python
from clabe.aind.watchdog import UploadGroup, WatchdogDataTransferService, WatchdogSettings

settings = WatchdogSettings(
    destination=r"\\allen\aind\scratch\vr-foraging\data",
    project_name="Cognitive flexibility in patch foraging",
    upload_groups=[
        UploadGroup(name="primary", job_type="vr_foraging_async_videos_0", creates_record=True, schedule_time=None),
        UploadGroup(
            name="videos",
            job_type="vr_foraging_async_videos_1",
            modalities=["behavior-videos"],
            delete_modalities_source_after_success=True,
        ),
    ],
)
transfer_service = WatchdogDataTransferService(launcher.session_directory, settings=settings, session=session)
transfer_service.transfer()
```

| Field | Default | Meaning |
| --- | --- | --- |
| `name` | required | Group name; also names its manifest |
| `job_type` | required | Transfer service job type |
| `modalities` | `"*"` | Modality folders, or `"*"` for every modality no other group claims |
| `creates_record` | `false` | Exactly one group: creates the metadata record and gets the `*.json` metadata and tags |
| `schedule_time` | `20:00` | When to submit; `null` = now |
| `delete_modalities_source_after_success` | `false` | Delete this group's folders once submitted |
| `upload_tasks` | `None` | Extra transfer service tasks |

- Each group is its own manifest: `manifest_<session>.yaml` for the record group, `manifest_<session>__<name>.yaml`
  for the others.
- Non-record groups append to the record, so their job types must support appending.
- A modality that no group claims is an error. A group that finds none of its modalities is skipped with a warning.
- The record group must be submitted first. An appending group with `schedule_time: null` is refused when the record
  group is scheduled later. An appending group scheduled at an earlier time of day only logs a warning.

Preview the manifests without writing them:

```python
for planned in transfer_service.plan():
    print(planned.group, planned.filename, list(planned.manifest.modalities))
```

## Data in several folders or on another machine

Pass `launcher.session_directories`, and give the other folders' modalities their own group. It lists this PC's
session folder, the other folders added with `launcher.add_session_directory` (e.g. Open Ephys's data folder on this
PC), and every other PC's folder registered with `remote.register_session`, through its share:

```python
transfer_service = WatchdogDataTransferService(
    launcher.session_directories,
    settings=WatchdogSettings(
        destination=r"\\allen\aind\scratch\vr-foraging\data",
        project_name="Cognitive flexibility in patch foraging",
        upload_groups=[
            UploadGroup(name="primary", job_type="vr_foraging_ephys_0", creates_record=True, schedule_time=None),
            UploadGroup(name="ecephys", job_type="vr_foraging_ephys_1", modalities=["ecephys"]),
        ],
    ),
    session=session,
)
```

- The data crosses the launcher's network, so schedule large modalities at night.
- The watchdog's account needs read access to the share, and write access if it deletes sources. Share a data folder
  rather than using `D$`.
