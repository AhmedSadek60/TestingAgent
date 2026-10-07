/** What went wrong in a call to the API, in the shape the server always uses: `{"error": {"kind", "message", "details"}}`. */
export class ApiError extends Error {
  readonly status: number;
  readonly kind: string;
  readonly details: { field: string; problem: string }[];

  constructor(status: number, kind: string, message: string, details: { field: string; problem: string }[] = []) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.kind = kind;
    this.details = details;
  }

  get unauthorized(): boolean {
    return this.status === 401;
  }
}

/** A failure of the network itself (the server is down, the connection was cut). */
export class NetworkError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "NetworkError";
  }
}

export function describeError(error: unknown): string {
  if (error instanceof ApiError || error instanceof NetworkError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}
