"use strict";

// Test fixture responses must finish while their document is still alive.
function createResponseBodyCapture(onBody) {
  const pending = new Set();
  const requests = new Map();
  const failures = [];
  let attemptedCount = 0;
  function requestStarted(request) {
    if (requests.has(request)) throw new Error('owned request was registered twice');
    let finish;
    const settled = new Promise(resolve => { finish = resolve; });
    requests.set(request, { request, settled, finish, finished: false });
  }
  function requestFinished(request) {
    const row = requests.get(request);
    if (!row) throw new Error('owned request finished without request-start registration');
    if (row.finished) throw new Error('owned request completed twice');
    row.finished = true;
    row.finish();
  }
  function requestFailed(request) {
    const failure = request.failure();
    failures.push({ url: request.url(), error: new Error(`owned request failed: ${failure?.errorText || 'unknown transport failure'}`) });
    requestFinished(request);
  }
  function capture(response) {
    attemptedCount += 1;
    let task;
    task = Promise.resolve()
      .then(() => response.body())
      .then((raw) => onBody(response, raw))
      .catch((error) => { failures.push({ url: response.url(), error }); })
      .finally(() => { pending.delete(task); });
    pending.add(task);
  }
  async function drain() {
    // Register at request-start, not only response arrival: a response may
    // arrive after the navigation barrier starts. Keep every body and failure.
    while (pending.size || [...requests.values()].some(row => !row.finished)) {
      await Promise.all([...pending, ...[...requests.values()].filter(row => !row.finished).map(row => row.settled)]);
    }
    if (failures.length) {
      throw new AggregateError(failures.map((row) => row.error),
        `response body capture failed: ${failures.map((row) => row.url).join(", ")}`);
    }
  }
  return { capture, drain, failures, requestStarted, requestFinished, requestFailed,
    get attemptedCount() { return attemptedCount; },
    get pendingCount() { return pending.size; },
    get pendingRequestCount() { return [...requests.values()].filter(row => !row.finished).length; },
    get trackedRequestCount() { return requests.size; } };
}

module.exports = { createResponseBodyCapture };
