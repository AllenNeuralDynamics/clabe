# Stores

A store reads and writes the records an experiment runs against: rigs, tasks, trainer state and per-animal settings.

```python
from clabe.session import SessionBuilder
from clabe.stores import Kind, LocalFileStore

RIG = Kind.from_rig(MyRigModel)
TASK = Kind.from_task(MyTaskModel)

session = SessionBuilder(launcher).build()
store = LocalFileStore(root=r"\\allen\aind\scratch\AindBehavior.db\MyProject")
store = store.scoped(subject=session.subject)

rig = store.resolve(RIG)  # asks the user if there is more than one
store.write(TASK, task_logic)
```

- Every backend has the same four methods. [`CompositeStore`][clabe.stores.CompositeStore] combines backends.
- See [Store Backends](store_backends.md) for each backend and how to combine them.

## Kinds

A [`Kind`][clabe.stores.Kind] names a record and the pydantic model it parses to. Declare them once, as module constants.

```python
from clabe.stores import Kind

RIG = Kind.from_rig(MyRigModel)
TASK = Kind.from_task(MyTaskModel)
SUGGESTION = Kind.from_trainer_state()
SESSION = Kind.from_session()
MANIPULATOR = Kind(ManipulatorPosition)  # a custom record, named "manipulator_position"
```

| Constructor | Name | Validates |
| --- | --- | --- |
| `Kind.from_rig(model)` | `"rig"` | The rig's computer name (override with `validators=`) |
| `Kind.from_task(model)` | `"task"` | — |
| `Kind.from_session()` | `"session"` | — (always `Session`) |
| `Kind.from_trainer_state()` | `"trainer_state"` | The trainer state has a stage |
| `Kind(model)` | snake-cased class name | — |

!!! warning
    `Kind(MyRigModel)` is named `"my_rig_model"` instead of `"rig"`, and no error is raised. Use the `from_*` constructors for framework records.

Add your own checks with `validators=` (one callable or a list, applied to every record read):

```python
from clabe.aind.validators import validate_rig_computer_name

STRICT_RIG = Kind.from_rig(MyRigModel, validators=[validate_rig_computer_name, my_extra_check])
```

## Methods

| Method | Prompts? | Use it for |
| --- | --- | --- |
| [`resolve(kind)`][clabe.stores.Store.resolve] | When there is a choice | Getting the one record to use now. |
| [`list(kind)`][clabe.stores.Store.list] | Never | Headless reads (servers, batch jobs, modifiers). |
| [`write(kind, value)`][clabe.stores.Store.write] | Never | Saving a record. The backend decides whether it overwrites or appends. |
| [`scoped(**scope)`][clabe.stores.Store.scoped] | — | A narrowed copy, e.g. to one animal. |

### resolve

```python
rig = store.resolve(RIG)
rig = store.resolve(RIG, pick_kwargs={"label": "Which rig is this session using?"})
```

- No match: raises `LookupError`.
- One match: returns it (and notifies the user).
- Several: asks the user through the active session; raises [`NoSessionError`][clabe.ui.NoSessionError] if nothing can ask (see [Talking to the user](frontends.md#when-nothing-can-ask)).
- `pick_kwargs` overrides the prompt's settings (label, `allow_none`, …). It does not change the options offered.

### list

```python
records = store.list(MANIPULATOR)
position = records[0] if records else default_position
```

### write

```python
store.write(SUGGESTION, trainer_state)
```

| Backend | A write… |
| --- | --- |
| `LocalFileStore` | Overwrites the file |
| `MemoryStore`, `DataverseStore` | Appends |
| `ConfiergeStore` | Goes to the ficus layer the scope names |

### scoped

```python
store = store.scoped(subject=session.subject)
rig = store.resolve(RIG)  # same as store.resolve(RIG, scope={"subject": session.subject})
```

- Returns a new store and leaves the original unchanged. Repeated calls add to the scope.
- Keys are plain strings (`subject`, `computer_name`, `task_name`, …). Each backend decides what they mean.

## Writing your own store

Subclass [`StoreBase`][clabe.stores.StoreBase] and implement two methods. `StoreBase` provides `resolve`, `list` and
`scoped`.

```python
from collections.abc import Sequence
from clabe.stores import Candidate, Kind, KindLike, Scope, StoreBase, as_kind


class MyStore(StoreBase):
    def _candidates(self, kind: Kind, scope: Scope) -> Sequence[Candidate]:
        """Every record of this kind in scope, each with a display label."""
        return [Candidate(label="...", value=...)]

    def write(self, kind: KindLike, value, *, scope: Scope | None = None) -> None:
        _kind = as_kind(kind)
        ...
```

Override `resolve` only when the backend can show something better than a pick list.
