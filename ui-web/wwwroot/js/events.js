// Follows the run's Server-Sent Events and hands each one to .NET. EventSource reconnects on its own and resumes
// after the last event it saw (Last-Event-ID). The stream of a served run ends at run_closed (its session closed);
// a run that is not kept open ends at run_finished, after which the server ends the stream.
const TYPES = [
  "run_started", "run_finished", "run_closing", "run_closed", "step_started", "step_finished", "step_failed",
  "step_skipped", "steps_invalidated", "artifact_exported", "prompt_opened", "prompt_closed", "notify", "header",
  "activity_started", "activity_ended", "activity_output", "branch_started", "branch_ended",
  "command_finished", "record_resolved",
];

export function follow(url, receiver) {
  const source = new EventSource(url);
  let finished = false;
  let over = false;
  let probing = false;

  const end = () => {
    if (over) return;
    over = true;
    source.close();
    receiver.invokeMethodAsync("OnStatus", "ended");
  };

  source.onopen = () => {
    if (!over) receiver.invokeMethodAsync("OnStatus", "live");
  };
  source.onerror = async () => {
    if (over) return;
    if (!finished) {
      if (source.readyState !== EventSource.CLOSED) receiver.invokeMethodAsync("OnStatus", "reconnecting");
      return;
    }
    // After the run: the server ended the stream (no closing screen) or went away. Keep following only while a
    // closing screen still waits for someone to close the session.
    if (probing) return;
    probing = true;
    try {
      if (!(await receiver.invokeMethodAsync("OnStreamLost"))) end();
    } finally {
      probing = false;
    }
  };
  for (const type of TYPES) {
    source.addEventListener(type, (message) => {
      receiver.invokeMethodAsync("OnEvent", message.data);
      if (type === "run_finished") finished = true;
      if (type === "run_closed") end();
    });
  }
  return { close: () => { over = true; source.close(); } };
}

// Follows the run process' log records (`log` events) and hands each one to .NET (OnLogLine). EventSource resumes
// after the last line it saw; the page closes this stream when the run's event stream ends.
export function followLogs(url, receiver) {
  const source = new EventSource(url);
  source.addEventListener("log", (message) => receiver.invokeMethodAsync("OnLogLine", message.data));
  return { close: () => source.close() };
}
