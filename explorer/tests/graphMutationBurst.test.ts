import assert from "node:assert/strict";
import test from "node:test";

import { GraphMutationBurst } from "../src/workspaces/GraphWorkspace/graphMutationBurst.ts";

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

test("a single mutation is applied and does not ask for a reload", async () => {
  const quiet: number[] = [];
  const burst = new GraphMutationBurst((count) => quiet.push(count), 20);
  assert.equal(burst.note(), "apply");
  await wait(40);
  assert.deepEqual(quiet, [1]);
});

test("a burst applies the first event and reports the full count once it is quiet", async () => {
  const quiet: number[] = [];
  const burst = new GraphMutationBurst((count) => quiet.push(count), 30);
  assert.equal(burst.note(), "apply");
  assert.equal(burst.note(), "hold");
  assert.equal(burst.note(), "hold");
  await wait(20);
  assert.deepEqual(quiet, []);
  await wait(30);
  assert.deepEqual(quiet, [3]);
});
