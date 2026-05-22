/**
 * Slice 15 — the brief §2.1 disclaimer footer. Mirrors the mobile
 * `DISCLAIMER_SHORT` so the staff dashboard carries the same safety
 * copy. Don't paraphrase — wording history in `docs/ux-copy.md`
 * documents that paraphrasing reintroduced "diagnose" once and was
 * reverted.
 */

import { DISCLAIMER_SHORT } from "../safety";

export function Footer() {
  return (
    <footer className="border-t bg-muted/40 py-4 text-center text-xs text-muted-foreground">
      {DISCLAIMER_SHORT}
    </footer>
  );
}
