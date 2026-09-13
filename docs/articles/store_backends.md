# Store Backends

This article works through every backend CLABE ships, and how to combine them with [`CompositeStore`][clabe.stores.CompositeStore]. See [Stores](stores.md) for the shared `Kind` / `Store` vocabulary these examples build on.

| Backend | Serves | Interactivity | Notes |
| --- | --- | --- | --- |
| [`LocalFileStore`][clabe.stores.LocalFileStore] | any kind | Pick list over matching files | The config library — a directory of JSON files, e.g. on a shared network drive |
| [`MemoryStore`][clabe.stores.MemoryStore] | any kind | Pick list over in-process records | Tests, and assembling records without touching disk |
| [`DataverseStore`][clabe.stores.dataverse.DataverseStore] | `trainer_state` only | Pick list over recent suggestions | Needs the `aind-services` extra |
| [`CompositeStore`][clabe.stores.CompositeStore] | whatever its routes serve | Delegates to the routed backend | Routes a kind name to a backend — the payoff over subclassing |

## LocalFileStore

A store over a directory of JSON files. Reads glob for matching files; writes overwrite and create any missing parent directories.

```python
from clabe.stores import Kind, LocalFileStore

RIG = Kind.from_rig(MyRigModel)
TASK = Kind.from_task(MyTaskModel)

store = LocalFileStore(root=r"\\allen\aind\scratch\AindBehavior.db\MyProject")

rig = store.resolve(RIG)
store.write(TASK, my_task_logic)
```

`computer_name` is filled into the store's scope automatically — from `COMPUTERNAME`, falling back to `platform.node()` — since resolving a rig config needs to know which machine is asking:

```python
store.scope  # {"computer_name": "RIG-01"}
```

### The on-disk layout

The default [`Layout`][clabe.stores.Layout] — [`DefaultLayout`][clabe.stores.DefaultLayout] — reproduces the config-library tree used today:

```text
<root>/
  Rig/<computer_name>/*.json
  Task/*.json
  Subjects/<subject>/task.json
  Subjects/<subject>/trainer_state.json
```

`task` reads the subject's folder first and falls back to the shared library — an ordered scope chain, rather than the nested try/while a picker used to do inline:

```python
store = store.scoped(subject="789012")
task_logic = store.resolve(TASK)  # Subjects/789012/task.json, else Task/*.json
```

Kinds other than `rig` and `task` are per-subject state living beside the subject's task; with no subject in scope they sit at the library root instead — which is what makes a one-off store pointed at a single directory work (see [Recovering a past session](#recovering-a-past-session) below).

### A custom layout

Pass your own `Layout` when the on-disk shape doesn't match `DefaultLayout` — implementing `read` (glob patterns, highest priority first) and `write` (the single path to persist to):

```python
from collections.abc import Sequence
from clabe.stores import LocalFileStore, Scope


class FlatLayout:
    """Every kind lives directly at the library root, no subject nesting."""

    def read(self, kind_name: str, scope: Scope) -> Sequence[str]:
        return [f"{kind_name}.json"]

    def write(self, kind_name: str, scope: Scope) -> str:
        return f"{kind_name}.json"


store = LocalFileStore(root="./my_lib", layout=FlatLayout())
```

## MemoryStore

Holds records in a process-local list. Nothing touches disk or a network, so it's the right fake for tests and for assembling records in code.

```python
from clabe.stores import Kind, MemoryStore

MANIPULATOR = Kind(ManipulatorPosition)

store = MemoryStore()
store.write(MANIPULATOR, ManipulatorPosition(x=1, y=2, z=3))

assert store.list(MANIPULATOR) == [ManipulatorPosition(x=1, y=2, z=3)]
```

Like `DataverseStore`, writes **append** rather than overwrite, so several records of one kind accumulate as candidates — useful for exercising `resolve`'s many-candidates prompt in a test without a real backend behind it. A record written under a scope is only visible to a read whose scope agrees with every one of those narrowings; a record written unscoped is visible everywhere:

```python
store.write(MANIPULATOR, ManipulatorPosition(x=0, y=0, z=0), scope={"subject": "123"})

store.list(MANIPULATOR)  # [] — no subject in scope
store.scoped(subject="123").list(MANIPULATOR)  # [ManipulatorPosition(x=0, y=0, z=0)]
```

## DataverseStore

A store over the Dataverse suggestion tables — the trainer-state history kept in `aibs_fact_mouse_proposed_behavior_sessionses`, keyed through `aibs_dim_mices`. It serves `trainer_state` only; resolving or writing any other kind raises `LookupError`.

`clabe.stores.dataverse` isn't imported by `clabe.stores` itself, since it needs `requests`, `msal` and `pyyaml` — install the extra and import it explicitly:

```bash
pip install "aind-clabe[aind-services]"
```

```python
from clabe.stores.dataverse import DataverseStore
from clabe.stores import Kind

SUGGESTION = Kind.from_trainer_state()

store = DataverseStore(history=5)  # offer the 5 most recent suggestions
store = store.scoped(subject="789012", task_name="MyTask")

trainer_state = store.resolve(SUGGESTION)
store.write(SUGGESTION, next_trainer_state)
```

`resolve` and `list` both require `subject` and `task_name` in scope — set them once with `scoped`, as above, rather than passing `scope=` on every call. At the default `history=1` the latest suggestion is used without prompting; raise it to offer a short pick list of recent history instead.

Credentials are a [`ServiceSettings`][clabe.services.ServiceSettings] subclass, so they resolve from the `dataverse` section of your known config files (or the environment) by default:

```yaml
dataverse:
  tenant_id: "..."
  client_id: "..."
  org: "your-org"
```

`username`/`password` are not meant for the YAML file — `DataverseStore()` with no `client` builds one from a KeePass entry (`svc_sipe` by default). Pass your own `client=_DataverseRestClient(...)` to bypass KeePass entirely, e.g. in tests.

## CompositeStore: routing by kind

The payoff composition buys over the old picker inheritance: a deployment keeping rigs and tasks on a network share but trainer state in Dataverse is a routing table, not a subclass.

```python
from clabe.stores import CompositeStore, Kind, LocalFileStore
from clabe.stores.dataverse import DataverseStore

RIG = Kind.from_rig(MyRigModel)
TASK = Kind.from_task(MyTaskModel)
SUGGESTION = Kind.from_trainer_state()

store = CompositeStore(
    default=LocalFileStore(root=r"\\allen\aind\scratch\AindBehavior.db\MyProject"),
    routes={"trainer_state": DataverseStore()},
)

rig = store.resolve(RIG)  # → LocalFileStore (the default)
trainer_state = store.resolve(SUGGESTION)  # → DataverseStore (routed by name)
```

Every method — `resolve`, `list`, `write`, `scoped` — delegates to whichever backend serves the kind, so each backend's own presentation is used (a flat file pick list for `RIG`, a queried table of recent suggestions for `SUGGESTION`). `scoped` narrows **every** backend at once:

```python
store = store.scoped(subject=session.subject)  # narrows both the local store and Dataverse
```

A kind with no route and no `default` raises `LookupError` naming the kind — which kinds a deployment actually serves is a fact assembled from configuration, not something the type system tracks.

## Recipes

### Scoping a store to the animal

```python
from clabe.session import SessionBuilder

session = SessionBuilder(launcher).build()
store = store.scoped(subject=session.subject)
```

### Per-animal reconfiguration with ByAnimalModifier

[`ByAnimalModifier`][clabe.modifiers.ByAnimalModifier] reads and writes per-animal state through a store, so the same modifier works against any backend:

```python
from clabe.modifiers import ByAnimalModifier
from clabe.stores import Kind

MANIPULATOR = Kind(ManipulatorPosition)


class ManipulatorModifier(ByAnimalModifier[MyRig]):
    def __init__(self, store):
        super().__init__(store, MANIPULATOR, "manipulator.position")

    def _process_before_dump(self):
        return read_position_from_hardware()


modifier = ManipulatorModifier(store.scoped(subject=session.subject))
rig = modifier.inject(rig)  # leaves the rig untouched if nothing is stored
...
modifier.dump()
```

### Recovering a past session

`Session` is serializable — a receipt of an acquisition — so `Kind.from_session()` exists, and a one-off `LocalFileStore` pointed straight at a session directory can load one back with no subject nesting involved:

```python
from clabe.stores import Kind, LocalFileStore

session = LocalFileStore(root=session_directory).resolve(Kind.from_session())
store = store.scoped(subject=session.subject)
```

### Testing against a store

`MemoryStore` needs no frontend for non-interactive calls, which makes it the right fake for exercising `list`/`write`-based code (like a modifier) without mocking a UI:

```python
from clabe.stores import Kind, MemoryStore

MANIPULATOR = Kind(ManipulatorPosition)


def test_inject_uses_the_stored_record():
    store = MemoryStore().scoped(subject="123")
    store.write(MANIPULATOR, ManipulatorPosition(x=1, y=2, z=3))

    rig = ManipulatorModifier(store).inject(MyRig())

    assert rig.manipulator.position == ManipulatorPosition(x=1, y=2, z=3)
```

Exercising `resolve` in a test does need a frontend — register a fake with [`ui.use_frontend`][clabe.ui.use_frontend] for the duration:

```python
from clabe import ui

with ui.use_frontend(fake_frontend):
    rig = store.resolve(RIG)
```
