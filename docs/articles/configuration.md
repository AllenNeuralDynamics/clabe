# Configuration

Stores, data transfer, logging and apps read their settings from a `clabe.yml` document, one section per
[`ServiceSettings`][clabe.services.ServiceSettings] subclass.

```python
from typing import ClassVar
from clabe.services import ServiceSettings


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

- Every section clabe ships: [ServiceSettings and yaml tags](service_settings.md).

## Where settings come from

Highest priority first; fields merge across sources and missing files or sections are skipped.

| # | Source |
| --- | --- |
| 1 | Keyword arguments, e.g. `MyServiceSettings(port=1)` |
| 2 | `./local/clabe.yml` |
| 3 | The in-memory document (`--clabe-yml` or `set_clabe_yml`) |
| 4 | `./clabe.yml` |
| 5 | `%PROGRAMDATA%/clabe.yml` |
| 6 | Environment variables and `.env` |

- Settings are read when the object is built; objects built earlier keep their values.

## Loading a clabe.yml from elsewhere

### From the command line

```bash
clabe run my_experiment.py --clabe-yml D:/rig-configs/clabe.yml
```

- Also settable through the `CLABE_YML` environment variable.
- Launcher flags (`--debug-mode`, `--frontend`, …) are read before this file, so pass them on the command line.

### From code

Call [`set_clabe_yml`][clabe.services.set_clabe_yml] with a parsed mapping, before any settings object is built:

```python
import requests
import yaml

from clabe.services import set_clabe_yml

response = requests.get("http://config-server/rigs/my-rig/clabe.yml", timeout=10)
response.raise_for_status()
set_clabe_yml(yaml.safe_load(response.text))

settings = MyServiceSettings()  # sees the fetched document
```

- Any mapping works, e.g. `set_clabe_yml(row["clabe_config"])` from a database.
- `set_clabe_yml(None)` removes it.
- `./local/clabe.yml` still wins, so one rig can override it while debugging.

## Temporary overrides

[`override_clabe_yml`][clabe.services.override_clabe_yml] swaps the document inside a `with` block only (per thread and
asyncio task), which suits tests:

```python
from clabe.services import get_clabe_yml, override_clabe_yml

with override_clabe_yml({"my_service": {"port": 9091}}):
    assert MyServiceSettings().port == 9091

with override_clabe_yml(None):  # files only
    settings = MyServiceSettings()

get_clabe_yml()  # the document currently in effect
```
