# Uploading data with the watchdog

[`WatchdogDataTransferService`][clabe.data_transfer.aind_watchdog.WatchdogDataTransferService] writes a *manifest* to
the [aind-watchdog-service](https://github.com/AllenNeuralDynamics/aind-watchdog-service) flag directory. At the
manifest's `schedule_time`, the watchdog copies the session to `destination` and submits its upload job.

By default a session is one upload job with every modality. Upload groups split it into several jobs that land in the
**same asset**, so slow modalities (e.g. video compression) no longer hold back the rest.

## Upload groups

In `clabe.yml`:

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
      schedule_time: "20:00"         # the default; can be left out
      delete_modalities_source_after_success: true
```

Or in code:

```python
from clabe.data_transfer.aind_watchdog import UploadGroup, WatchdogDataTransferService, WatchdogSettings

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

Each group is written as its own manifest:

- **`creates_record`**: exactly one group. It creates the metadata record and gets the `*.json` metadata files and data
  description tags. Manifest: `manifest_<session>.yaml`.
- **Other groups** append to that record, so their job types must support appending. Manifest:
  `manifest_<session>__<name>.yaml`.
- **`modalities`**: modality folder names, or `"*"` (default) for every modality no other group claims. A modality
  claimed by no group is an error.
- **`job_type`** is required. `schedule_time` (default 20:00, `null` = now), `delete_modalities_source_after_success`
  and `upload_tasks` are optional.
- A group that finds none of its modalities is skipped with a warning. Listed modalities a group doesn't find are logged
  at info level.

The top-level `job_type`, `schedule_time`, `delete_modalities_source_after_success` and `upload_tasks` are deprecated.
Without `upload_groups` they still describe a single group, with a warning. With `upload_groups` they are ignored and a
warning is logged, including when code changes them after the settings are loaded. To migrate, move them into one
group:

```yaml
watchdog:
  upload_groups:
    - name: primary
      job_type: vr_foraging_v2
      creates_record: true
      delete_modalities_source_after_success: true
```

Code that overrides these fields (e.g. `settings.job_type = "upload_only_v2"`) must pass its own `upload_groups`
instead.

**Order.** The group that creates the record is written first. A group that appends can't be submitted before it:
`schedule_time: null` while the record is scheduled later is refused, and an earlier time of day only warns (it may
run after midnight).

To check the manifests without writing them:

```python
for planned in transfer_service.plan():
    print(planned.group, planned.filename, list(planned.manifest.modalities))
```

## Data recorded on another machine

Pass the other machine's session folder through its share. The launcher's watchdog reads it over the network, and its
modalities can get their own group:

```python
transfer_service = WatchdogDataTransferService(
    [launcher.session_directory, remote.session_directory_unc],
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

- The data crosses the launcher's network on its way to `destination`, so schedule large modalities at night.
- The watchdog's account needs read access to the share, and write access if it deletes the sources. Prefer a share of
  the data folder over the administrative share (`D$`).
