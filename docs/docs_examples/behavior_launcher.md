# AIND experiment

A behavior session that reads the subject's trainer state and the rig from a config store, runs two task apps at the
same time, then runs the curriculum and maps the session to the data schema. The store, the apps and the data mapper
are mocks, so it runs on any PC:

```bash
uv run clabe run examples/behavior_launcher.py --allow-dirty --skip-hardware-validation
```

```python
--8<-- "examples/behavior_launcher.py"
```
