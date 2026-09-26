import { describe, expect, it } from "vitest";
import { formatCount, formatDate, formatDateTime, formatTime } from "./format";

/** These assert exact strings on purpose. The whole point of this module is that the output does
 * not vary with the host's locale or time zone -- a test that allowed either would not catch the
 * hydration bug it exists to prevent. */
describe("display formatting is deterministic", () => {
  const iso = "2026-09-26T14:30:05Z";

  it("formats a date unambiguously, with a short month name", () => {
    // en-GB renders September as "Sept", not "Sep" -- that is real ICU output, not a typo.
    expect(formatDate(iso)).toBe("26 Sept 2026");
  });

  it("formats a date and time in UTC, and says UTC", () => {
    expect(formatDateTime(iso)).toBe("26 Sept 2026, 14:30 UTC");
  });

  it("formats a time in 24h UTC", () => {
    expect(formatTime(iso)).toBe("14:30 UTC");
  });

  it("does not shift with the caller's time zone", () => {
    // 23:30 in +05:30 is still the same instant: 18:00 UTC.
    expect(formatDateTime("2026-09-26T23:30:00+05:30")).toBe("26 Sept 2026, 18:00 UTC");
  });

  it("pins the thousands separator", () => {
    expect(formatCount(1234567)).toBe("1,234,567");
  });

  it("accepts a Date as well as an ISO string", () => {
    expect(formatDate(new Date(iso))).toBe("26 Sept 2026");
  });
});
