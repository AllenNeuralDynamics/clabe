# Stores in CLABE

A **store** is how CLABE reads and writes the records an experiment runs against — rigs, tasks, trainer state, and any per-animal reconfiguration. It replaces the old picker classes, which fused data access, user prompting and `Session` construction into one object and then subclassed themselves sideways every time a *single* record needed a different backend.

A store is composition instead: one small protocol — [`resolve`][clabe.stores.Store.resolve], [`list`][clabe.stores.Store.list], [`write`][clabe.stores.Store.write], [`scoped`][clabe.stores.Store.scoped] — implemented once per backend (local files, Dataverse, an in-memory fake, …) and combined per deployment with [`CompositeStore`][clabe.stores.CompositeStore] rather than an inheritance chain. See [Store Backends](store_backends.md) for concrete examples of every backend and of composing them; this article covers the shared vocabulary.

## Kind: naming a record

Every store call needs to know *which* record it's after and *what shape* it deserializes to. [`Kind`][clabe.stores.Kind] carries both:

```python
from clabe.stores import Kind

RIG = Kind.from_rig(MyRigModel)
TASK = Kind.from_task(MyTaskModel)
SUGGESTION = Kind.from_trainer_state()
MANIPULATOR = Kind(ManipulatorPosition)  # a custom, project-specific record
```

Declare kinds once per project — as module-level constants, the way `RIG` and `SUGGESTION` are declared in the examples — and pass the same instance to every store.

The canonical kinds fix the framework's own vocabulary, regardless of which model class you hand them:

| Constructor | Name | Equivalent to | Notes |
| --- | --- | --- | --- |
| `Kind.from_rig(model)` | `"rig"` | `Kind(model, "rig", validators=validate_rig_computer_name)` | Override the validator with `validators=` |
| `Kind.from_task(model)` | `"task"` | `Kind(model, "task")` | |
| `Kind.from_session()` | `"session"` | `Kind(Session, "session")` | Always `Session` — there is no per-project session model |
| `Kind.from_trainer_state()` | `"trainer_state"` | `Kind(TrainerState, "trainer_state", validators=...)` | The validator rejects a stage-less `TrainerState`; it's internal, so this one you can't spell out yourself |

Two rig subclasses used on different rigs still share `name="rig"`, which is the point: they route to the same backend and the same on-disk layout, regardless of which pydantic model each rig computer happens to validate against.

!!! warning "The bare constructor does not snake-case to a framework name"
    `Kind(MyRigModel)` is named `"my_rig_model"`, **not** `"rig"` — the bare constructor derives the name from the model class and is meant for project-specific records that have no framework-fixed name (like `MANIPULATOR` above). Reach for `from_rig` / `from_task` / `from_session` / `from_trainer_state` whenever you mean a canonical record; the bare form deriving the "wrong" name is easy to do by accident and easy to miss, since nothing raises. We mostly do this for backwards compatibility and to make sure multiple derived classes share the same underlying name.

A `Kind` can also carry `validators` — one callable, or a sequence of them, applied in order to every record a store hands back through `resolve` or `list`:

```python
from clabe.stores import Kind

SUGGESTION = Kind.from_trainer_state()  # already validates for a real stage

STRICT_RIG = Kind(MyRigModel, "rig", validators=[validate_rig_computer_name, my_extra_check])
```

## The Store protocol

```python
class Store(Protocol):
    def resolve(self, kind, *, scope=None, pick_kwargs=None) -> T: ...
    def list(self, kind, *, scope=None) -> Sequence[T]: ...
    def write(self, kind, value, *, scope=None) -> None: ...
    def scoped(self, **scope: str) -> "Store": ...
```

| Method | Interactive? | Use it for |
| --- | --- | --- |
| `resolve` | Yes, when ambiguous | The main entry point — "give me *the* rig / task / trainer state to use now". |
| `list` | Never | Headless reads: an RPC server, batch reprocessing, or a modifier reading per-animal state without prompting. |
| `write` | Never | Persisting a record. Whether that overwrites, versions or appends is the *backend's* contract, not something the caller decides. |
| `scoped` | — | Narrows every subsequent call, e.g. to one animal. Returns a new store; the original is untouched. |

### resolve — the interactive read

```python
rig = store.resolve(RIG)
```

`resolve` raises `LookupError` when nothing matches, silently returns the one candidate when there's exactly one (with a `notify()` so the choice is still visible), and otherwise prompts — through whichever [frontend](frontends.md) is currently registered. Because presentation is backend-specific (a flat pick list for local files, a queried table for Dataverse, …), `resolve` lives on the store itself rather than a separate "resolver" object layered on top.

Prompting needs an answer, so if no frontend is registered `resolve` raises [`NoFrontendError`][clabe.ui.NoFrontendError] instead of hanging — the same rule every `prompt_*` in `clabe.ui` follows. See [Frontends → Talking to the active frontend](frontends.md#talking-to-the-active-frontend).

Pass `pick_kwargs` to override the prompt shown when there's a choice to make (the label, whether "none" is offered, …); `options` itself always comes from the store's own candidates and can't be overridden:

```python
store.resolve(RIG, pick_kwargs={"label": "Which rig is this session using?"})
```

### list — the headless read

```python
records = store.list(MANIPULATOR)
position = records[0] if records else default_position
```

Never prompts, so it's the right primitive for anything that has to make a choice — or accept there isn't one — without a human watching. This is what [`ByAnimalModifier`][clabe.modifiers.ByAnimalModifier] uses to inject per-animal state, silently falling back to a default when nothing is stored.

### write — the non-interactive write

```python
store.write(SUGGESTION, trainer_state)
```

No confirmation prompt, ever — whether a write clobbers, versions or appends is the backend's contract (a local file overwrites; `DataverseStore` and `MemoryStore` append), decided once per backend rather than asked of the experimenter at 9am.

### scoped — narrowing

```python
store = store.scoped(subject=session.subject)
rig = store.resolve(RIG)  # only this animal's records are visible
# equivalent to: store.resolve(RIG, scope={"subject": session.subject})
store.write(SUGGESTION, next_state)
```

`scoped` returns a new store sharing the same underlying data, narrowed by the given key/value pairs; the store it was called on is untouched. Scope keys are plain strings (`{"subject": "789012", "computer_name": "RIG-01", ...}`) and their meaning is entirely up to the backend — a `LocalFileStore` turns them into path segments, `DataverseStore` turns `subject` and `task_name` into an OData filter. Call `scoped` again to narrow further; each call layers on top of what came before.

## Writing your own store

Most backends should subclass [`StoreBase`][clabe.stores.StoreBase], which supplies scope-merging and the default `resolve` policy (raise on none, auto-select one, prompt otherwise) so a new backend only has to implement two things: how to enumerate candidates, and how to write one.

```python
from collections.abc import Sequence
from clabe.stores import Candidate, Kind, KindLike, Scope, StoreBase, as_kind


class MyStore(StoreBase):
    def _candidates(self, kind: Kind, scope: Scope) -> Sequence[Candidate]:
        """Return every record of this kind visible in scope, each labelled for display."""
        ...

    def write(self, kind: KindLike, value, *, scope: Scope | None = None) -> None:
        """Persist a record. See ``Store.write``."""
        _kind = as_kind(kind)
        ...
```

Override `resolve` itself only when the backend affords a materially better presentation than a flat pick list — a queried table, search-as-you-type, a file browser — the way a future backend might. `list` and `scoped` come free from `StoreBase` and rarely need overriding.

## See also

- [Store Backends](store_backends.md) — worked examples for every backend CLABE ships, and for combining them with `CompositeStore`.
- [Frontends](frontends.md) — what actually renders a `resolve` prompt, and how a store degrades without one.
