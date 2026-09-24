# Configuration

Most configurable parts of clabe — stores, data transfer, logging, apps — take their settings from a
[`ServiceSettings`][clabe.services.ServiceSettings] subclass. Each subclass declares a `__yml_section__` and
reads that section of a `clabe.yml` document. See [ServiceSettings and yaml tags](service_settings.md) for the
full list of sections.

```python
class MyServiceSettings(ServiceSettings):
    __yml_section__: ClassVar[str] = "my_service"

    host: str = "localhost"
    port: int = 8080
```

```yaml
my_service:
  host: my-host
  port: 9090
```

## Where settings come from

Settings are resolved every time a settings object is constructed, from these sources, highest priority first:

1. Keyword arguments, e.g. `MyServiceSettings(port=1)`
2. `./local/clabe.yml`
3. The **in-memory document**, if one is installed (see below)
4. `./clabe.yml`
5. `%PROGRAMDATA%/clabe.yml`
6. Environment variables and `.env`

Sources are deep-merged per field: a field missing from a higher-priority source falls through to the next one,
and a missing section or file is simply skipped.

## Loading configuration from somewhere else

The known config files are fixed paths. If your configuration lives elsewhere — a config server, a database, a
network share — fetch it yourself at startup and install it with [`set_clabe_yml`][clabe.services.set_clabe_yml]. The
document has the same shape as a `clabe.yml` file: top-level keys are `__yml_section__` names.

Call it **before** any settings object is built, typically at the top of your launcher script. Settings created
earlier keep the values they were built with.

### From the command line

Every launcher accepts `--clabe-yml`, which reads a `clabe.yml` from any path and installs it as the in-memory
document:

```bash
clabe run my_experiment.py --clabe-yml D:/rig-configs/clabe.yml
```

The same works for scripts that parse [`LauncherCliArgs`][clabe.launcher.LauncherCliArgs] themselves, and
`clabe serve` forwards the flag to the experiment it starts. Like any other launcher setting, it can also be set
through the `CLABE_YML` environment variable.

The file is applied to every settings object created after the arguments are parsed. The launcher's own
arguments (`--debug-mode`, `--frontend`, ...) have already been read at that point, so set those on the command
line rather than in this file.

### Over HTTP

```python
import requests
import yaml

from clabe.services import set_clabe_yml

response = requests.get("http://config-server/rigs/my-rig/clabe.yml", timeout=10)
response.raise_for_status()
set_clabe_yml(yaml.safe_load(response.text))

# From here on, every ServiceSettings subclass sees the fetched document
settings = MyServiceSettings()
```

### From an arbitrary file

The same works for a `clabe.yml` anywhere on the file system, for example a path chosen per rig:

```python
import os
from pathlib import Path

import yaml

from clabe.services import set_clabe_yml

config_path = Path(os.environ.get("CLABE_CONFIG", "D:/rig-configs/clabe.yml"))
set_clabe_yml(yaml.safe_load(config_path.read_text(encoding="utf-8")))
```

Any source that yields a mapping works, e.g. a database row already holding the document as a dict:
`set_clabe_yml(row["clabe_config"])`. Pass `None` to remove the document again.

Because the in-memory document ranks below `./local/clabe.yml`, a local file on a single rig can still override
the fetched configuration, e.g. while debugging.

## Temporary overrides

[`override_clabe_yml`][clabe.services.override_clabe_yml] replaces the installed document inside a `with` block only, and
restores it on exit. The override is held in a `ContextVar`, so it is invisible to other threads and asyncio
tasks — handy in tests:

```python
from clabe.services import override_clabe_yml

with override_clabe_yml({"my_service": {"port": 9091}}):
    assert MyServiceSettings().port == 9091

with override_clabe_yml(None):  # ignore the in-memory document, files only
    settings = MyServiceSettings()
```

[`get_clabe_yml`][clabe.services.get_clabe_yml] returns whichever document is currently in effect.
