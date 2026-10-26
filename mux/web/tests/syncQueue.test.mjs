import assert from 'node:assert/strict';
import test from 'node:test';

import { enqueueSync } from '../src/lib/syncQueue.ts';

test('a failed sync rejects its caller but does not block the next sync', async () => {
  const failure = new Error('write failed');
  const reported = [];
  const first = enqueueSync(Promise.resolve(), async () => {
    throw failure;
  }, error => reported.push(error));

  await assert.rejects(first.result, failure);
  await first.queue;
  assert.deepEqual(reported, [failure]);

  const writes = [];
  const second = enqueueSync(first.queue, async () => {
    writes.push('written');
  }, error => reported.push(error));
  await second.result;
  await second.queue;

  assert.deepEqual(writes, ['written']);
  assert.deepEqual(reported, [failure]);
});
