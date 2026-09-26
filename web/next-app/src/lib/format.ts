/**
 * Deterministic display formatting for server-rendered values.
 *
 * Every one of these was a hydration bug. `toLocaleString()` and friends resolve the locale
 * *and* the time zone from whatever host they run on: Node picks the server's, the browser picks
 * the visitor's. A date the server rendered as "Sep 26, 2026" came back from the browser as
 * "26 Sept 2026", React saw two different trees, and threw away the server HTML for that subtree
 * ("Hydration failed because the server rendered text didn't match the client"). Numbers have the
 * same hazard, quietly: `1234..toLocaleString()` is "1,234" in en-US and "1 234" in fr-FR.
 *
 * So both the locale and the time zone are pinned here, and nothing in this app calls
 * `toLocale*` directly. `en-GB` with a short month name is deliberately unambiguous -- "26 Sep
 * 2026" cannot be misread the way 09/26 vs 26/09 can, which matters for an audit trail.
 *
 * Times are UTC and say so. That is the honest choice for a multi-tenant tool whose audit log,
 * sessions and share-link expiries are compared across time zones; showing each viewer their own
 * local time would mean two people reading the same audit row and disagreeing about when it
 * happened. If per-viewer local time is ever wanted, it has to be rendered client-side only
 * (after mount), never during SSR.
 */

const LOCALE = "en-GB";
const TIME_ZONE = "UTC";

const DATE = new Intl.DateTimeFormat(LOCALE, {
  day: "2-digit",
  month: "short",
  year: "numeric",
  timeZone: TIME_ZONE,
});

const DATE_TIME = new Intl.DateTimeFormat(LOCALE, {
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: TIME_ZONE,
});

const TIME = new Intl.DateTimeFormat(LOCALE, {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: TIME_ZONE,
});

const NUMBER = new Intl.NumberFormat(LOCALE);

/** "26 Sep 2026" */
export function formatDate(value: string | Date): string {
  return DATE.format(new Date(value));
}

/** "26 Sep 2026, 14:30 UTC" */
export function formatDateTime(value: string | Date): string {
  return `${DATE_TIME.format(new Date(value))} UTC`;
}

/** "14:30 UTC" */
export function formatTime(value: string | Date): string {
  return `${TIME.format(new Date(value))} UTC`;
}

/** "1,234" -- pinned so SSR and the browser agree on the separator. */
export function formatCount(value: number): string {
  return NUMBER.format(value);
}
