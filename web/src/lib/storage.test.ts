import { afterEach, describe, expect, it, vi } from "vitest";
import { PROJECT_KEY, readStored, TOKEN_KEY, writeStored } from "./storage";

afterEach(() => vi.restoreAllMocks());

describe("browser storage", () => {
  it("remembers a value in the area it was given", () => {
    writeStored("local", PROJECT_KEY, "alpha");
    writeStored("session", TOKEN_KEY, "tab-only");
    expect(readStored("local", PROJECT_KEY)).toBe("alpha");
    expect(window.localStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(window.sessionStorage.getItem(TOKEN_KEY)).toBe("tab-only");
  });

  it("forgets a value when it is cleared", () => {
    writeStored("local", PROJECT_KEY, "alpha");
    writeStored("local", PROJECT_KEY, null);
    expect(readStored("local", PROJECT_KEY)).toBeNull();
  });

  it("works without storage: reads give nothing, writes are silently dropped", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("full", "QuotaExceededError");
    });
    expect(readStored("local", PROJECT_KEY)).toBeNull();
    expect(() => writeStored("local", PROJECT_KEY, "x")).not.toThrow();
  });

  it("works when the browser refuses to hand out the storage object at all", () => {
    vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    expect(readStored("local", PROJECT_KEY)).toBeNull();
    expect(() => writeStored("local", PROJECT_KEY, "x")).not.toThrow();
  });
});
