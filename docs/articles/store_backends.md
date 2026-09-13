# Store Backends

This article works through every backend CLABE ships, and how to combine them with [`CompositeStore`][clabe.stores.CompositeStore]. See [Stores](stores.md) for the shared `Kind` / `Store` vocabulary these examples build on.

| Backend | Serves | Interactivity | Notes |
| --- | --- | --- | --- |
| [`LocalFileStore`][clabe.stores.LocalFileStore] | any kind | Pick list over matching files | The config library — a directory of JSON files, e.g. on a shared network drive |
| [`MemoryStore`][clabe.stores.MemoryStore] | any kind | Pick list over in-process records | Tests, and assembling records without touching disk |
| [`DataverseStore`][clabe.stores.dataverse.DataverseStore] | `trainer_state` only | Pick list over recent suggestions | Needs the `aind-services` extra |
| [`FicusStore`][clabe.stores.ficus.FicusStore] | `rig` as layered config, any other kind as a flat record | Never prompts — the chain yields exactly one document | Plain HTTP over ficus; no extra needed |
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

## FicusStore

[Ficus](https://github.com/AllenNeuralDynamics/ficus) is a layered configuration service. It stores documents addressed by a `namespace`, a `filename` and an optional *scope* — a computer, a subject — and an effective config is assembled from a `defaults` document plus one document per scope, deep-merged in a fixed precedence order. `FicusStore` reads and writes one namespace of it.

There is no third-party client involved: clabe talks to ficus over plain HTTP through its own [`FicusClient`][clabe.stores.ficus.FicusClient]. Since `requests` is already a core dependency, this backend needs **no optional extra** — unlike `dataverse`, `pip install aind-clabe` is enough, and `FicusStore`, `FicusSettings`, `FicusClient` and `MergePolicy` are re-exported straight from `clabe.stores`:

```python
from clabe.stores import FicusSettings, FicusStore, Kind

RIG = Kind.from_rig(AindVrForagingRig)
MANIPULATOR = Kind(ManipulatorPosition)

store = FicusStore(FicusSettings(namespace="aind-behavior-vr-foraging"))

rig = store.resolve(RIG)
position = store.scoped(subject="789907").resolve(MANIPULATOR)
```

Settings are a [`ServiceSettings`][clabe.services.ServiceSettings] subclass, so they resolve from the `ficus` section of your known config files (or the environment) by default:

```yaml
ficus:
  namespace: aind-behavior-vr-foraging
  base_url: http://eng-tools/ficus-dev
  config_kinds: ["rig"]
  config_scopes: ["computers"]
```

`base_url` defaults to `http://eng-tools/ficus-dev`, and like every other setting can be overridden in the `ficus` section of clabe.yml.

### Two shapes of document

`FicusStore` serves two shapes of document, told apart by kind name:

- **Layered config** — any kind named in `config_kinds`, which defaults to `{"rig"}`. Merged across `defaults` and then the configured scopes, from ficus' `default` document.
- **Flat records** — every other kind. A standalone document named `<kind.name>.json`, living in exactly *one* scope, read and written whole. It never joins a merge chain. This is what [`ByAnimalModifier`][clabe.modifiers.ByAnimalModifier] uses for per-animal state.

Concretely, in the `aind-behavior-vr-foraging` namespace on rig `DT201256` running subject `789907`, the rig comes out of a merge ending at `/scratch/computers/DT201256/aind-behavior-vr-foraging/default.json`, while the manipulator position is the single document `/scratch/subjects/789907/aind-behavior-vr-foraging/manipulator_position.json`.

Either shape yields exactly one document, so `resolve` never prompts: `_candidates` returns at most one `Candidate`, which `StoreBase.resolve` auto-selects.

The merge chain for a config kind runs lowest precedence first:

```text
defaults/default.json
defaults/<filename>                  # only when FicusSettings.filename is set
<scope>/<id>/default.json
<scope>/<id>/<filename>              # only when FicusSettings.filename is set
```

`filename` is an *extra* document layered on top of `default.json` at every level — a variant (`high-freq.json`) rather than a replacement. Left unset, the chain is `default.json` alone.

### Extensions

Ficus accepts `.json`, `.yml` and `.yaml`, and a document is stored under whichever one created it. A read matches the extension exactly: a request for `default.json` when `default.yml` is what exists returns "not found". Ficus also allows a stem only one extension at a time — writing `default.json` alongside an existing `default.yml` returns 409.

Each `default` document in the chain is looked up via [`FicusClient.locate`][clabe.stores.ficus.FicusClient.locate], which tries `FicusSettings.extension` first and falls back through the others. Layers may differ from one another: `defaults` as `.json` and one rig as `.yml` merges normally.

A document that exists keeps its extension. `FicusSettings.extension` (`.json` by default) names only documents that do not exist yet:

```python
FicusSettings(namespace=..., extension=".yml")
```

When the preferred extension is the stored one, a lookup is a single request; a miss costs one request per remaining extension. Flat-record writes perform this lookup before writing.

The `filename` extra document is fetched by exact name, extension included.

### The merge happens client-side

Ficus can merge layers itself. `FicusStore` does not use that: every layer is fetched individually with `merge=false` and merged client-side.

The server's merge returns 404 for the entire request if any requested scope has no document, and requires a named file to exist at the `defaults` layer. Merging client-side skips a missing layer instead, and records which documents contributed — [`MergeResult.sources`][clabe.stores.ficus.MergeResult] lists them in the order applied, and that list labels the resulting `Candidate`.

The merge reproduces ficus' `_deep_update`: dicts present on both sides recurse, and everything else — lists included — is replaced outright.

`null` is a **value, not a deletion**. A downstream layer can blank out a field it inherited: if `defaults` sets `manipulator.port: "COM3"` and rig `DT201256` has no manipulator, the rig layer writes `manipulator: null` and the merged config resolves to `null`. Under RFC 7386 semantics the key would be removed instead, and the value would fall back to the pydantic field's default, which is not always `None`.

[`MergePolicy`][clabe.stores.ficus.MergePolicy] exposes these as `null_means`, `lists`, `on_type_conflict` and `on_missing_layer`; the defaults reproduce ficus' behaviour:

```python
from clabe.stores import FicusSettings, MergePolicy

settings = FicusSettings(
    namespace="aind-behavior-vr-foraging",
    policy=MergePolicy(lists="concat", on_type_conflict="raise"),
)
```

### Reads and writes address a scope differently

From ficus' OpenAPI spec:

```text
POST|PATCH|DELETE  /v1/computers/{hostname}/namespaces/{namespace}/config/{filename}
POST|PATCH|DELETE  /v1/subjects/{subject_id}/namespaces/{namespace}/config/{filename}
GET                /v1/namespaces/{namespace}/config?hostname=&subject_id=&filename=&merge=
```

A scope has a **collection** (`computers`, `subjects`) and a **parameter** (`hostname`, `subject_id`). Writes use both: the collection is the path segment, the parameter is the path variable. There is no scoped `GET` route — a `GET` on a write path returns `405 Method Not Allowed` — so reads address a scope by query parameter on the unscoped route.

An unrecognised query parameter is ignored rather than rejected: `?computers=DT201256` returns the `defaults` layer.

[`ScopeRef`][clabe.stores.ficus.ScopeRef] carries both names; `COMPUTERS` / `SUBJECTS` bind them to an identifier.

### Which rig, and which subject

The rig identifier is the **machine name**, held as the `computer_name` scope key. It is seeded at construction from [`get_computer_name`][clabe.utils.get_computer_name], as it is for `LocalFileStore`, and narrowed like any other scope key:

```python
store.scope                              # {"computer_name": "RIG-01"}
store.scoped(computer_name="DT201256")   # address another machine's layer
```

`CompositeStore.scoped` passes the narrowing to every backend it routes to, so one call moves them together.

`subject` behaves the same way: `scoped(subject="789907")` puts a flat record under `subjects/<id>`; with no subject in scope it goes under the rig.

The machine name is not `aibs_comp_id`, which holds the AIND *rig* name (`FRG.4A`). The two are different identifiers for the same machine, and ficus is keyed on the former — `/scratch/computers/DT201256/...`, `/scratch/computers/LEVIATHON/...`.

To drop the rig layer, leave it out of `config_scopes`:

```python
FicusSettings(namespace=..., config_scopes=[])   # defaults only
```

### The subject scope is not in the default merge chain

`config_scopes` defaults to `["computers"]`, so the subject layer does not contribute to the merged rig even when a subject is in scope. Per-animal values reach the rig through [`ByAnimalModifier`][clabe.modifiers.ByAnimalModifier], which reads a flat record and injects it; including the subject layer here as well would apply them twice.

To have ficus own per-animal rig overrides instead, add it:

```python
settings = FicusSettings(
    namespace="aind-behavior-vr-foraging",
    config_scopes=["computers", "subjects"],  # subject wins over rig, which wins over defaults
)
```

### Writing a flat record

A flat record has exactly one home and is written whole. `write` `POST`s and falls back to `PATCH` on a 409; ficus' `PATCH` deep-merges the payload into that document server-side, so a partial write needs no read-modify-write:

```python
store.scoped(subject="789907").write(MANIPULATOR, ManipulatorPosition(x=1, y=2, z=3))
# → /scratch/subjects/789907/aind-behavior-vr-foraging/manipulator_position.json
```

Because `PATCH` merges, it cannot *remove* a key. Dropping a field takes a delete followed by a rewrite.

### Writing layered config back

Layered config has no single home. `write` diffs the value against what the config currently resolves to and sends **only the leaves that changed**, to the top of the merge chain:

```python
rig = store.resolve(RIG)
rig.manipulator.port = "COM4"
store.write(RIG, rig)
# → PATCH /scratch/computers/DT201256/aind-behavior-vr-foraging/default.json
# → body: {"manipulator": {"port": "COM4"}}
```

When nothing differs, nothing is written.

Both sides of the diff are put through the same model first. A stored document omits every field the model defaults and may carry keys the model does not declare; a model dump re-introduces the former and drops the latter. Normalising both sides cancels those differences, leaving only the edit.

Two cases raise:

- **A key present in the resolved config but absent from the value.** `PATCH` merges and cannot delete, and `null` is a value here rather than a tombstone. Removing a key takes a delete and a rewrite.
- **A write a higher layer would shadow.** The merge records which layer won each leaf ([`MergeResult.origin`][clabe.stores.ficus.MergeResult.origin]). A write aimed at `defaults` for a value the rig layer overrides would not change what the next read returns. This cannot arise at the default target, the top of the chain.

[`WritePolicy`][clabe.stores.ficus.WritePolicy] governs both.

With an empty `config_scopes` the only remaining layer is `defaults`, which every machine in the namespace reads; `write` raises rather than write it.

To target one layer explicitly:

```python
from clabe.stores.ficus import COMPUTERS, LayerKey

store.client.write(                      # write this document outright
    store.namespace,
    {"manipulator": {"port": "COM4"}},
    scope=COMPUTERS("DT201256"),
)

current = store.client.get_merged(store.namespace, scopes=[COMPUTERS("DT201256")])
store.client.write_back(                 # or diff against the chain, into a layer you name
    store.namespace,
    current,
    new_config,
    target=LayerKey(None, "default.json"),   # None scope == the defaults layer
)
```

[`plan_write_back`][clabe.stores.ficus.FicusClient.plan_write_back] runs the same logic and returns the [`LayerWrite`][clabe.stores.ficus.LayerWrite] without sending it.

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
    # a ficus-backed rig route (routes={"rig": FicusStore(...)}) is the same shape
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

The subject is a constructor argument, and the modifier narrows the store with it. A store with no subject in scope reads and writes wherever the backend places a subject-less record, which for `FicusStore` is the rig's own scope.

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
