/**
 * Ontology ingest emits one websocket message per node and edge. Applying each
 * message resets the layout timer and freezes the page. The first event in a
 * quiet window is applied immediately; a burst is held and reloaded once.
 */
export class GraphMutationBurst {
  private count = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private readonly onQuiet: (count: number) => void;
  private readonly quietMs: number;

  constructor(onQuiet: (count: number) => void, quietMs = 400) {
    this.onQuiet = onQuiet;
    this.quietMs = quietMs;
  }

  note(): "apply" | "hold" {
    this.count += 1;
    if (this.timer !== null) {
      clearTimeout(this.timer);
    }
    const decision = this.count === 1 ? "apply" : "hold";
    this.timer = setTimeout(() => {
      const seen = this.count;
      this.count = 0;
      this.timer = null;
      this.onQuiet(seen);
    }, this.quietMs);
    return decision;
  }

  dispose(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.count = 0;
  }
}
