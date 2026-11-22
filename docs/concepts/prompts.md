# Prompts, sessions and clients

Experiments ask people things — which animal, does this look right, retry? — and show them things. CLABE separates
three concerns so that any screen, or an agent, can do the asking.

## Requests: what to ask

A **request** describes a prompt: `TextRequest`, `NumberRequest` (bounds, step, slider), `ConfirmRequest`,
`PickRequest`, `AutoCompleteRequest`, `PathRequest`, `FormRequest` (a pydantic model), `FieldRequest` (one field of a
model), `GroupRequest` (several prompts answered together), `ReadOnlyTable`, `AcknowledgeRequest`,
`MarkdownRequest` and `MediaRequest` (images, HTML, web pages).

A request is self-describing. It knows its **kind**, its **spec** (what to show, as JSON), the **answer schema** (the
JSON Schema of a valid answer) and how to **validate** an answer and convert it to its Python type. Because the rules
live in the request, every client applies the same ones, and a client in another language (the web UI) or an agent
needs only the JSON.

`ui.text(...)`, `ui.pick(...)`, `ui.form(...)` and friends build a request and ask it; `ui.ask(request)` asks one you
built. The answer comes back validated and typed (a `Path`, an enum member, a model instance).

## Sessions: who is asking

Code never holds a screen. `ui.ask` asks whatever **session** is bound now:

- while a workflow runs, the **run** — it opens a prompt, publishes it to every client, and waits for a valid answer;
- outside a run (before it starts, at the exit prompt), the launcher's **client session** around its local client.

With nothing bound, `ui.ask` raises `NoSessionError` and `ui.notify` logs the message instead, so library code that
talks to the user also works standalone.

## Clients: who answers

A **client** shows prompts and messages and collects raw answers: the terminal UI (Textual), the console, the web UI,
an MCP or AG-UI agent, or a headless client that only waits (for runs started by the daemon). Clients never validate;
the run does, and an invalid answer is rejected with a reason the client shows before asking again.

A run has any number of clients. All of them see every prompt; the **first valid answer wins**, and the others see
the prompt close with who answered it. This is what lets an experimenter start a session on the rig and answer from
the corridor, or an agent fill in what it knows while a person watches.

## Showing things

`ui.notify(message, level)` informs; `ui.header(...)` titles; `ui.activity(...)` shows that something long is going
on. Inside a run they are events, recorded and streamed to every client. Prompts that only show something
(`ui.markdown`, `ui.image`, `ui.html`, `ui.url`, `ui.acknowledge`) wait for the user to continue; a client that
cannot show a kind of content (the terminal and an image) warns and names the file instead of skipping it.

## Notifications versus logging

A notification is for a person, now; a log record is for diagnosis, later. Both exist and neither replaces the
other: see [Logging](../articles/logging.md).

## Secrets

A `SecretStr` field is entered like any other, but its value is masked wherever an answer is recorded or streamed.
