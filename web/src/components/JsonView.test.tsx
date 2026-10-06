import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { JsonView, RedactedText, Untrusted } from "./JsonView";

const HOSTILE = '<img src=x onerror="window.__pwned = true"><script>window.__pwned = true</script><b>bold</b><a href="javascript:alert(1)">link</a>';

function nothingRan(): boolean {
  return (window as unknown as { __pwned?: boolean }).__pwned === undefined;
}

describe("RedactedText", () => {
  it("makes what the server hid visible, and says what the marker means", () => {
    const { container } = render(
      <p>
        <RedactedText text="key=[REDACTED:api_key] and [REDACTED] more" />
      </p>,
    );
    const marked = [...container.querySelectorAll(".redacted")];
    expect(marked.map((node) => node.textContent)).toEqual(["[REDACTED:api_key]", "[REDACTED]"]);
    expect(marked[0]).toHaveAttribute("title", "The server hid a secret here before storing it");
    expect(container.textContent).toBe("key=[REDACTED:api_key] and [REDACTED] more");
  });

  it("does not take text that merely starts like a marker for one", () => {
    const { container } = render(<RedactedText text="[REDACTED but never closed" />);
    expect(container.querySelector(".redacted")).toBeNull();
    expect(container.textContent).toBe("[REDACTED but never closed");
  });

  it("shows markup as text, never as markup", () => {
    const { container } = render(<RedactedText text={HOSTILE} />);
    expect(container.querySelector("img, script, b, a")).toBeNull();
    expect(container.textContent).toBe(HOSTILE);
    expect(nothingRan()).toBe(true);
  });
});

describe("Untrusted", () => {
  it("labels what the agent said as untrusted and shows it as text", () => {
    const { container } = render(<Untrusted text={HOSTILE} />);
    expect(screen.getByText("From the agent under test · untrusted, shown as text")).toBeInTheDocument();
    expect(container.querySelector("img, script, b, a")).toBeNull();
    expect(container.querySelector("pre")?.textContent).toBe(HOSTILE);
    expect(nothingRan()).toBe(true);
  });

  it("takes another label", () => {
    render(<Untrusted text="x" label="From the document" />);
    expect(screen.getByText("From the document · untrusted, shown as text")).toBeInTheDocument();
  });

  it("shows the server's redaction markers", () => {
    const { container } = render(<Untrusted text="token [REDACTED:bearer]" />);
    expect(container.querySelector(".redacted")?.textContent).toBe("[REDACTED:bearer]");
  });
});

describe("JsonView", () => {
  it("shows each kind of value in its own way", () => {
    render(<JsonView value={{ name: "x", count: 3, ok: true, nothing: null, items: [], extra: {} }} />);
    const view = screen.getByRole("group", { name: "Structured data" });
    expect(view).toHaveTextContent('name: "x"');
    expect(view).toHaveTextContent("count: 3");
    expect(view).toHaveTextContent("ok: true");
    expect(view).toHaveTextContent("nothing: null");
    expect(view).toHaveTextContent("items: []");
    expect(view).toHaveTextContent("extra: {}");
  });

  it("shows how many entries each list or object has, and opens only the first levels", () => {
    const { container } = render(<JsonView value={{ a: { b: { c: [1, 2, 3] } } }} />);
    const details = [...container.querySelectorAll("details")];
    expect(details.map((d) => d.querySelector("summary")?.textContent)).toEqual(["{1}", "a: {1}", "b: {1}", "c: [3]"]);
    expect(details.map((d) => d.open)).toEqual([true, true, false, false]);
  });

  it("shows a long string cut, and the whole of it when asked", async () => {
    const text = `${"a".repeat(1200)}${"b".repeat(1800)}`;
    render(<JsonView value={{ text }} />);
    const view = screen.getByRole("group", { name: "Structured data" });
    expect(view.textContent).not.toContain("b");
    await userEvent.click(within(view).getByRole("button", { name: "show all 3,000 characters" }));
    expect(view.textContent).toContain("b".repeat(1800));
    expect(within(view).queryByRole("button")).not.toBeInTheDocument();
  });

  it("says how many entries it left out of a very long list", () => {
    render(<JsonView value={Array.from({ length: 105 }, (_, i) => i)} />);
    expect(screen.getByText("… and 5 more")).toBeInTheDocument();
  });

  it("shows markup inside the data as text", () => {
    const { container } = render(<JsonView value={{ reply: HOSTILE, [HOSTILE]: "key" }} />);
    expect(container.querySelector("img, script, b, a")).toBeNull();
    expect(container.textContent).toContain(HOSTILE);
    expect(nothingRan()).toBe(true);
  });

  it("shows what the server redacted", () => {
    const { container } = render(<JsonView value={{ authorization: "Bearer [REDACTED:bearer]" }} />);
    expect(container.querySelector(".redacted")?.textContent).toBe("[REDACTED:bearer]");
  });
});
