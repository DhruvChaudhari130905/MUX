// Run an operation after the current queue while preserving its rejection for the caller.
// The internal queue recovers so one failed sync doesn't block later file updates.
export function enqueueSync<T>(
  queue: Promise<void>,
  operation: () => Promise<T>,
  onError: (error: unknown) => void,
): { queue: Promise<void>; result: Promise<T> } {
  const result = queue.then(operation);
  const next = result.then(
    () => undefined,
    error => {
      onError(error);
    },
  );
  return { queue: next, result };
}
