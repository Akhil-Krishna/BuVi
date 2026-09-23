import { describe, expect, it } from "vitest";
import { issuedCookieOptions, shouldSetSecureCookie } from "./cookie-options";

describe("shouldSetSecureCookie", () => {
  it("is on in production", () => {
    expect(shouldSetSecureCookie("production")).toBe(true);
  });

  it("is off in development and test, where the dev server speaks plain HTTP", () => {
    // Not a preference: a `Secure` cookie set over HTTP is silently dropped by
    // the browser, which would break the whole login flow locally.
    expect(shouldSetSecureCookie("development")).toBe(false);
    expect(shouldSetSecureCookie("test")).toBe(false);
  });

  it("fails closed to off when NODE_ENV is unset rather than assuming production", () => {
    expect(shouldSetSecureCookie(undefined)).toBe(false);
  });
});

describe("issuedCookieOptions", () => {
  it("always sets HttpOnly, SameSite=Lax and a root path", () => {
    const options = issuedCookieOptions(60);
    expect(options.httpOnly).toBe(true);
    expect(options.sameSite).toBe("lax");
    expect(options.path).toBe("/");
    expect(options.maxAge).toBe(60);
  });

  it("carries no Domain attribute -- this app issues host-only cookies (ADR 0018)", () => {
    expect(issuedCookieOptions()).not.toHaveProperty("domain");
  });

  it("derives `secure` from the live NODE_ENV rather than a constant", () => {
    const original = process.env.NODE_ENV;
    try {
      // @ts-expect-error -- NODE_ENV is readonly in the Next.js types
      process.env.NODE_ENV = "production";
      expect(issuedCookieOptions().secure).toBe(true);
      // @ts-expect-error -- as above
      process.env.NODE_ENV = "development";
      expect(issuedCookieOptions().secure).toBe(false);
    } finally {
      // @ts-expect-error -- as above
      process.env.NODE_ENV = original;
    }
  });
});
