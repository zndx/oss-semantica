/**
 * The graph query is cached for the life of the page. A later ontology load
 * grows the server graph without replacing that cache, so the canvas can stay
 * at 0 nodes while a temporal snapshot already reports the new nodes as active.
 */
export function shouldReloadGraphForServerGrowth({
  loadedNodeCount,
  activeNodeCount,
  busy,
}: {
  loadedNodeCount: number | null;
  activeNodeCount: number | null;
  busy: boolean;
}): boolean {
  if (busy) return false;
  if (loadedNodeCount === null || activeNodeCount === null) return false;
  return activeNodeCount > loadedNodeCount;
}
