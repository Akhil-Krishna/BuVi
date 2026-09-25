import type { Aggregation } from "./types";

/** Section 8.3's v1 grammar: `AGG([DISTINCT] column)`. Composed here from the
 * builder's own dropdowns, never free-typed -- kept out of `actions.ts`
 * (a `"use server"` module, where every export must be an async server
 * action) since this is pure string formatting, not a server call. */
export function composeExpression(
  aggregation: Aggregation,
  distinct: boolean,
  column: string
): string {
  const agg = aggregation.toUpperCase();
  return distinct ? `${agg}(DISTINCT ${column})` : `${agg}(${column})`;
}
