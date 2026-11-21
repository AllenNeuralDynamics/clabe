// Follows the run's Server-Sent Events and hands each one to .NET. EventSource reconnects on its own and resumes
// after the last event it saw (Last-Event-ID); the stream is closed once the run has finished.
const TYPES = [
  "run_started", "run_finished", "step_started", "step_finished", "step_failed", "step_skipped",
  "steps_invalidated", "artifact_exported", "prompt_opened", "prompt_closed", "notify", "header",
  "activity_started", "activity_ended",
];

export function follow(url, receiver) {
  const source = new EventSource(url);
  source.onopen = () => receiver.invokeMethodAsync("OnStatus", "live");
  source.onerror = () => {
    if (source.readyState !== EventSource.CLOSED) receiver.invokeMethodAsync("OnStatus", "reconnecting");
  };
  for (const type of TYPES) {
    source.addEventListener(type, (message) => {
      receiver.invokeMethodAsync("OnEvent", message.data);
      if (type === "run_finished") {
        source.close();
        receiver.invokeMethodAsync("OnStatus", "ended");
      }
    });
  }
  return { close: () => source.close() };
}
