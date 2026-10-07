import { describe, expect, it } from "vitest";
import { ApiError, describeError, NetworkError } from "./errors";

describe("ApiError", () => {
  it("carries what the server said", () => {
    const error = new ApiError(422, "validation", "The request is not valid.", [{ field: "name", problem: "required" }]);
    expect(error).toBeInstanceOf(Error);
    expect(error.name).toBe("ApiError");
    expect(error.status).toBe(422);
    expect(error.kind).toBe("validation");
    expect(error.message).toBe("The request is not valid.");
    expect(error.details).toEqual([{ field: "name", problem: "required" }]);
  });

  it("has no details unless given some", () => {
    expect(new ApiError(500, "error", "boom").details).toEqual([]);
  });

  it("knows when the token was refused", () => {
    expect(new ApiError(401, "unauthorized", "no").unauthorized).toBe(true);
    expect(new ApiError(403, "forbidden", "no").unauthorized).toBe(false);
  });
});

describe("describeError", () => {
  it("uses the message of the server's and the network's errors", () => {
    expect(describeError(new ApiError(404, "not_found", "No such run."))).toBe("No such run.");
    expect(describeError(new NetworkError("Cannot reach the AgentLab server. Is it running?"))).toBe("Cannot reach the AgentLab server. Is it running?");
  });

  it("uses the message of any other error", () => {
    expect(describeError(new TypeError("x is not a function"))).toBe("x is not a function");
  });

  it("has something to say about what is not an error at all", () => {
    expect(describeError("text")).toBe("Something went wrong.");
    expect(describeError(undefined)).toBe("Something went wrong.");
    expect(describeError({ message: "looks like one" })).toBe("Something went wrong.");
  });
});
