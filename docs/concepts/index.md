# Concepts

How clabe's pieces relate, in pictures. Terms are defined in the [glossary](glossary.md).

## How it fits together

```mermaid
flowchart LR
  wf["Workflow"] -- "clabe run" --> launcher["Launcher"] --> run["Run"]
  run --> records[("Records<br/>events, outputs, run.json")]
  run <-->|"prompts, answers"| clients["Clients<br/>TUI, web UI, agents"]
  run --- server["Run server (HTTP)"]
  daemon["Daemon (rig PC)"] -- "starts, proxies" --> server
  hub["Hub (optional)"] -- "follows" --> daemon
```

## Steps and runs

```mermaid
flowchart LR
  run["Run"] -- executes --> wf["Workflow"] -- "a list of" --> step["Step"]
  step -- "each execution:<br/>an attempt" --> output["Output"]
  output -. "input of later steps,<br/>by name" .-> step
```

- A step's **name** is its output's name; its **parameters** name the outputs it needs.
- Built-ins every step can take: `launcher`, `run`.

A step's states:

```mermaid
stateDiagram-v2
  direction LR
  [*] --> pending
  pending --> running
  running --> done
  running --> skipped: optional, declined
  running --> failed: raised
  failed --> running: Retry
  done --> pending: go back
  done --> locked: a later barrier ran
```

Going back to `rig` re-runs only what depends on it (highlighted):

```mermaid
flowchart LR
  session --> store --> trainer_state --> review
  rig --> review --> register
  session --> register
  rig --> register
  classDef rerun fill:#cceb05,stroke:#141d28,color:#000
  class rig,review,register rerun
```

## Prompts and clients

Every client sees every prompt; the run validates; the first valid answer wins.

```mermaid
sequenceDiagram
  participant S as Step
  participant R as Run
  participant T as TUI (rig)
  participant W as Web UI
  participant A as Agent
  S->>R: ui.number("Trials")
  R-->>T: prompt
  R-->>W: prompt
  R-->>A: prompt
  A->>R: "abc"
  R-->>A: rejected: not a number
  W->>R: 300
  R->>S: 300
  R-->>T: closed (answered from the web UI)
```

## Records

```mermaid
flowchart TB
  run["Run"] --> events["events.jsonl<br/>what happened"] & outputs["outputs.jsonl<br/>outputs + lineage"] & artifacts["artifacts/"] & manifest["run.json<br/>code, env, machine"]
  events --> replay["clabe replay"]
```

## Machines

```mermaid
flowchart TB
  browser["Browser"] & agent["Agent (clabe mcp)"] --> hub["Hub"]
  hub --> a & b
  subgraph pcA["Rig PC A"]
    a["Daemon (host)"] -- "/run/" --> run["Run server"]
  end
  subgraph pcB["Rig PC B"]
    b["Daemon (worker)"] --> ephys["Open Ephys"]
  end
  run -- "lease, jobs" --> b
```

- Connections go from the observer to the rig, never the other way.
- A worker serves one run at a time (its **lease**).
