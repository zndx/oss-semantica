import assert from "node:assert/strict";
import test from "node:test";

import { shouldReloadGraphForServerGrowth } from "../src/workspaces/GraphWorkspace/graphServerGrowth.ts";

test("a larger server graph reloads a smaller cached canvas", () => {
  assert.equal(shouldReloadGraphForServerGrowth({
    loadedNodeCount: 0,
    activeNodeCount: 8636,
    busy: false,
  }), true);
});

test("an in-flight load does not start another reload", () => {
  assert.equal(shouldReloadGraphForServerGrowth({
    loadedNodeCount: 0,
    activeNodeCount: 8636,
    busy: true,
  }), false);
});

test("the canvas is left alone when it already holds every active node", () => {
  assert.equal(shouldReloadGraphForServerGrowth({
    loadedNodeCount: 8636,
    activeNodeCount: 8636,
    busy: false,
  }), false);
  assert.equal(shouldReloadGraphForServerGrowth({
    loadedNodeCount: 8636,
    activeNodeCount: 100,
    busy: false,
  }), false);
});

test("missing counts do not reload", () => {
  assert.equal(shouldReloadGraphForServerGrowth({
    loadedNodeCount: null,
    activeNodeCount: 8636,
    busy: false,
  }), false);
  assert.equal(shouldReloadGraphForServerGrowth({
    loadedNodeCount: 0,
    activeNodeCount: null,
    busy: false,
  }), false);
});
