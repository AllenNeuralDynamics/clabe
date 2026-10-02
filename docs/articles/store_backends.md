# Store Backends

This article works through every backend CLABE ships, and how to combine them with [`CompositeStore`][clabe.stores.CompositeStore]. See [Stores](stores.md) for the shared `Kind` / `Store` vocabulary these examples build on.

| Backend | Serves | Interactivity | Notes |
| --- | --- | --- | --- |
| [`LocalFileStore`][clabe.stores.LocalFileStore] | any kind | Pick list over matching files | The config library — a directory of JSON files, e.g. on a shared network drive |
| [`MemoryStore`][clabe.stores.MemoryStore] | any kind | Pick list over in-process records | Tests, and assembling records without touching disk |
| [`DataverseStore`][clabe.stores.dataverse.DataverseStore] | `trainer_state` only | Pick list over recent suggestions | Needs the `aind-services` extra |
| [`ConfiergeStore`][clabe.stores.confierge.ConfiergeStore] | one ficus namespace, whatever the kind | Never prompts — ficus merges the layers | Needs the `aind-services` extra |
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

## ConfiergeStore

[Ficus](https://github.com/AllenNeuralDynamics/ficus) is a layered configuration service: documents live under a `namespace` and are overridden per scope — a computer, a subject — with the effective config merged server-side. `ConfiergeStore` is a thin adapter between the store API and ficus' `confierge` client. It translates scopes, and leaves merging, scope validation and the offline cache to ficus and confierge.

It needs the `aind-services` extra, so it is imported explicitly:

```python
from clabe.stores import Kind
from clabe.stores.confierge import ConfiergeSettings, ConfiergeStore

RIG = Kind.from_rig(AindVrForagingRig)

store = ConfiergeStore(ConfiergeSettings(namespace="aind-behavior-vr-foraging"))

rig = store.resolve(RIG)
rig = store.scoped(subject="789907").resolve(RIG)  # adds the subject's overrides
store.scoped(subject="789907").write(RIG, rig)
```

A read yields exactly one document, so it never prompts. If ficus is unreachable, confierge serves its last cached copy of the same request.

Settings are a [`ServiceSettings`][clabe.services.ServiceSettings] subclass and resolve from the `confierge` section of your known config files:

```yaml
confierge:
  namespace: aind-behavior-vr-foraging
  base_url: http://eng-tools/ficus-dev  # optional; else $FICUS_BASE_URL, else confierge's default
  mode: null                            # optional ficus mode
```

`overwrite_defaults`, `create_if_missing` and `append_new_fields_to_last_scope` are passed through to ficus on every write.

### Scope mapping

The store's scope keys are not ficus' scope names. [`DEFAULT_SCOPE_MAPPING`][clabe.stores.confierge.DEFAULT_SCOPE_MAPPING] translates one into the other:

| Store scope key | Ficus scope |
| --- | --- |
| `computer_name` | `hostname` |
| `subject` | `subject_id` |

`computer_name` is filled in with this machine's name, as `LocalFileStore` does. It is the machine name, not the AIND rig name (`aibs_comp_id`, e.g. `FRG.4A`): ficus is keyed on the former. Scope keys outside the mapping, such as `task_name`, are not sent, so one scope can be shared across several stores.

To address a scope ficus gains later, extend the mapping:

```python
from clabe.stores.confierge import DEFAULT_SCOPE_MAPPING

store = ConfiergeStore(settings, scope_mapping={**DEFAULT_SCOPE_MAPPING, "rig": "rig_id"})
```

### One namespace per store

A store addresses a single namespace whatever the kind, so resolving two kinds from one store returns the same document. To serve several kinds, give each its own namespace and route them with [`CompositeStore`](#compositestore-routing-by-kind):

```python
store = CompositeStore(
    routes={
        "rig": ConfiergeStore(ConfiergeSettings(namespace="vr-foraging-rig")),
        "task": ConfiergeStore(ConfiergeSettings(namespace="vr-foraging-task")),
    },
)
```

A record must serialize to a JSON object to be written.

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
    # a ficus-backed rig route (routes={"rig": ConfiergeStore(...)}) is the same shape
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
    def __init__(self, subject, store):
        super().__init__(subject, store, MANIPULATOR, "manipulator.position")

    def _process_before_update(self):
        return read_position_from_hardware()


modifier = ManipulatorModifier(session.subject, store)
rig = modifier.inject(rig)  # leaves the rig untouched if nothing is stored
...
modifier.update()
```

The subject is a constructor argument, and the modifier narrows the store with it. A store with no subject in scope reads and writes wherever the backend places a subject-less record, which for `ConfiergeStore` is the computer's own scope.

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
