import { describe, expect, it } from "vitest";
import { parseSetCookie, findCookie } from "./cookie-relay";

describe("parseSetCookie", () => {
  it("extracts the value and max-age, ignoring Domain/Path attributes", () => {
    const header = "buvi_session=abc123; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=604800";
    expect(parseSetCookie(header, "buvi_session")).toEqual({
      value: "abc123",
      maxAgeSeconds: 604800,
    });
  });

  it("returns null for a different cookie name", () => {
    const header = "other_cookie=abc123; HttpOnly";
    expect(parseSetCookie(header, "buvi_session")).toBeNull();
  });

  it("handles a cookie value that itself contains an '=' (base64-ish)", () => {
    const header = "buvi_oidc_txn=YWJj===; HttpOnly; SameSite=Lax";
    expect(parseSetCookie(header, "buvi_oidc_txn")?.value).toBe("YWJj===");
  });

  it("tolerates a missing Max-Age", () => {
    const header = "buvi_session=abc123; HttpOnly";
    expect(parseSetCookie(header, "buvi_session")).toEqual({
      value: "abc123",
      maxAgeSeconds: undefined,
    });
  });
});

describe("findCookie", () => {
  it("finds the named cookie among several Set-Cookie headers", () => {
    const response = new Response(null, {
      headers: [
        ["set-cookie", "buvi_oidc_txn=xyz; HttpOnly"],
        ["set-cookie", "buvi_session=abc; HttpOnly; Max-Age=60"],
      ],
    });
    expect(findCookie(response, "buvi_session")).toEqual({ value: "abc", maxAgeSeconds: 60 });
  });

  it("returns null when the cookie is absent", () => {
    const response = new Response(null, { headers: [["set-cookie", "other=1"]] });
    expect(findCookie(response, "buvi_session")).toBeNull();
  });
});
