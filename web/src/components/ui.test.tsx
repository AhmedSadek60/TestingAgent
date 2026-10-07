import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AsyncState } from "../hooks/useAsync";
import {
  Async,
  BarRow,
  Badge,
  ConfirmDialog,
  CopyButton,
  Drawer,
  ErrorNote,
  Field,
  GradeBadge,
  KeyValue,
  Modal,
  Notice,
  ProgressBar,
  ScoreRing,
  SeverityBadge,
  StatusBadge,
  ToastProvider,
  useToast,
} from "./ui";

afterEach(() => {
  vi.useRealTimers();
  document.body.style.overflow = "";
});

describe("badges", () => {
  it("shows a grade's letter, and what qualifies it on hover", () => {
    render(<GradeBadge grade="F (capped by security; partial coverage)" />);
    const badge = screen.getByText("F");
    expect(badge).toHaveClass("bad");
    expect(badge).toHaveAttribute("title", "Qualified: capped by security; partial coverage");
  });

  it("shows a plain grade with nothing to qualify", () => {
    render(<GradeBadge grade="B" />);
    expect(screen.getByText("B")).toHaveClass("good");
    expect(screen.getByText("B")).not.toHaveAttribute("title");
  });

  it("says when there is no grade", () => {
    render(<GradeBadge grade={null} />);
    expect(screen.getByText("not graded")).toBeInTheDocument();
  });

  it("shows a run that is going as live, and says what each state is in words", () => {
    const { container, rerender } = render(<StatusBadge status="running" />);
    expect(container.querySelector(".badge")).toHaveClass("pulse");
    rerender(<StatusBadge status="stopped_due_to_cost" />);
    expect(screen.getByText("Stopped: cost limit")).toBeInTheDocument();
    rerender(<StatusBadge status={undefined} />);
    expect(screen.getByText("unknown")).toBeInTheDocument();
  });

  it("tells a severity from the lack of one", () => {
    const { rerender } = render(<SeverityBadge severity="critical" />);
    expect(screen.getByText("critical")).toHaveClass("critical");
    rerender(<SeverityBadge severity={null} />);
    expect(screen.getByText("none")).toBeInTheDocument();
  });

  it("takes a title", () => {
    render(<Badge title="Why">x</Badge>);
    expect(screen.getByText("x")).toHaveAttribute("title", "Why");
  });
});

describe("ScoreRing", () => {
  it("says the score and grade to a screen reader, and shows them", () => {
    render(<ScoreRing score={84.2} grade="B" />);
    expect(screen.getByRole("img", { name: "Score 84.2 of 100, grade B" })).toBeInTheDocument();
    expect(screen.getByText("84.2")).toBeInTheDocument();
    expect(screen.getByText("Grade B")).toBeInTheDocument();
  });

  it("keeps a qualified grade to its letter and says the qualifiers elsewhere", () => {
    const { container } = render(<ScoreRing score={40} grade="F (capped by security; partial coverage)" />);
    expect(screen.getByRole("img", { name: "Score 40 of 100, grade F, qualified: capped by security; partial coverage" })).toBeInTheDocument();
    expect(screen.getByText("Grade F")).toBeInTheDocument();
    expect(container.firstElementChild).toHaveAttribute("title", "Qualified: capped by security; partial coverage");
  });

  it("says it is not scored when there is no score", () => {
    render(<ScoreRing score={null} />);
    expect(screen.getByRole("img", { name: "Not scored" })).toBeInTheDocument();
    expect(screen.getByText("n/a")).toBeInTheDocument();
  });

  it("shows only the letter when it is small", () => {
    render(<ScoreRing score={90} grade="A" size={84} />);
    expect(screen.getByText("A")).toBeInTheDocument();
    expect(screen.queryByText("Grade A")).not.toBeInTheDocument();
  });
});

describe("ProgressBar and BarRow", () => {
  it("never shows more than full or less than empty", () => {
    const { rerender } = render(<ProgressBar value={140} label="Too much" />);
    expect(screen.getByRole("progressbar", { name: "Too much" }).firstElementChild).toHaveStyle({ width: "100%" });
    rerender(<ProgressBar value={-5} label="Too little" />);
    expect(screen.getByRole("progressbar", { name: "Too little" }).firstElementChild).toHaveStyle({ width: "0%" });
  });

  it("says its value to a screen reader", () => {
    render(<ProgressBar value={42.4} label="Coverage" />);
    const bar = screen.getByRole("progressbar", { name: "Coverage" });
    expect(bar).toHaveAttribute("aria-valuenow", "42");
    expect(bar).toHaveAttribute("aria-valuemax", "100");
  });

  it("shows a category's score, or why there is none", () => {
    const { rerender } = render(<BarRow label="Security" value={91} confidence={0.8} />);
    expect(screen.getByRole("progressbar", { name: "Security: 91" })).toBeInTheDocument();
    expect(screen.getByText("91")).toHaveAttribute("title", "Confidence 80%");
    rerender(<BarRow label="Security" value={null} note="Not tested: no tools" />);
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.getByText("n/a")).toBeInTheDocument();
    expect(screen.getAllByText("Not tested: no tools").length).toBeGreaterThan(0);
  });
});

describe("notices", () => {
  it("tells an error to assistive technology at once, and anything else politely", () => {
    const { rerender } = render(<Notice tone="error">Broken</Notice>);
    expect(screen.getByRole("alert")).toHaveTextContent("Broken");
    rerender(<Notice tone="info">Fyi</Notice>);
    expect(screen.getByRole("status")).toHaveTextContent("Fyi");
  });

  it("offers to try again when there is a way to", async () => {
    const retry = vi.fn();
    render(<ErrorNote error="Cannot reach it" onRetry={retry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Cannot reach it");
    await userEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it("does not offer it when there is not", () => {
    render(<ErrorNote error="Gone" />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("Async", () => {
  const state = (overrides: Partial<AsyncState<string>>): AsyncState<string> => ({
    data: null,
    error: null,
    loading: false,
    reload: () => undefined,
    setData: () => undefined,
    ...overrides,
  });

  it("shows that it is loading until there is something to show", () => {
    render(<Async state={state({ loading: true })} loading="Loading the runs…">{(data) => <p>{data}</p>}</Async>);
    expect(screen.getByRole("status")).toHaveTextContent("Loading the runs…");
  });

  it("shows the data, and keeps showing it when a later reload fails", () => {
    render(<Async state={state({ data: "the runs", error: "blip" })}>{(data) => <p>{data}</p>}</Async>);
    expect(screen.getByText("the runs")).toBeInTheDocument();
    expect(screen.queryByText("blip")).not.toBeInTheDocument();
  });

  it("shows the problem, with a way to try again, when there is nothing else", async () => {
    const reload = vi.fn();
    render(<Async state={state({ error: "Cannot reach it", reload })}>{(data) => <p>{data}</p>}</Async>);
    await userEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(reload).toHaveBeenCalledTimes(1);
  });
});

describe("Field and KeyValue", () => {
  it("ties the label to its input and shows its help and its problem", () => {
    render(
      <Field label="Name" htmlFor="n" help="What to call it" error="Required">
        <input id="n" />
      </Field>,
    );
    expect(screen.getByLabelText("Name")).toBeInTheDocument();
    expect(screen.getByText("What to call it")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Required");
  });

  it("shows n/a for a value that is not there", () => {
    render(<KeyValue items={[["Model", "gpt-x"], ["Cost", null]]} />);
    expect(screen.getByText("gpt-x")).toBeInTheDocument();
    expect(screen.getByText("n/a")).toBeInTheDocument();
  });
});

describe("dialogs", () => {
  it("puts focus inside, closes on Escape, holds the page still meanwhile, and gives focus back", async () => {
    const onClose = vi.fn();
    function Page() {
      return (
        <>
          <button type="button">opener</button>
          <Modal title="Stop the run?" onClose={onClose}>
            <button type="button">inside</button>
          </Modal>
        </>
      );
    }
    const opener = document.createElement("button");
    document.body.appendChild(opener);
    opener.focus();
    const { unmount } = render(<Page />);
    const dialog = screen.getByRole("dialog", { name: "Stop the run?" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog.contains(document.activeElement)).toBe(true);
    expect(document.body.style.overflow).toBe("hidden");
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
    unmount();
    expect(document.body.style.overflow).toBe("");
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });

  it("keeps Tab inside the dialog", async () => {
    render(
      <Drawer title="Details" onClose={() => undefined}>
        <button type="button">one</button>
        <button type="button">two</button>
      </Drawer>,
    );
    const user = userEvent.setup();
    const close = screen.getByRole("button", { name: "Close" });
    const two = screen.getByRole("button", { name: "two" });
    expect(document.activeElement).toBe(close);
    await user.tab({ shift: true });
    expect(document.activeElement).toBe(two);
    await user.tab();
    expect(document.activeElement).toBe(close);
  });

  it("closes on a click on the backdrop but not on a click inside", async () => {
    const onClose = vi.fn();
    render(
      <Drawer title="Details" onClose={onClose}>
        <p>content</p>
      </Drawer>,
    );
    await userEvent.click(screen.getByText("content"));
    expect(onClose).not.toHaveBeenCalled();
    const backdrop = screen.getByRole("dialog").parentElement!;
    await userEvent.pointer([{ target: backdrop }, { keys: "[MouseLeft]", target: backdrop }]);
    expect(onClose).toHaveBeenCalled();
  });

  it("asks before it does something, and lets the person keep things as they were", async () => {
    const onConfirm = vi.fn();
    const onClose = vi.fn();
    render(
      <ConfirmDialog title="Delete it?" confirmLabel="Delete" danger onConfirm={onConfirm} onClose={onClose}>
        <p>This cannot be undone.</p>
      </ConfirmDialog>,
    );
    const dialog = screen.getByRole("dialog", { name: "Delete it?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Keep it" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("cannot be confirmed twice while it is working", () => {
    render(
      <ConfirmDialog title="Delete it?" confirmLabel="Delete" busy onConfirm={() => undefined} onClose={() => undefined}>
        <p>x</p>
      </ConfirmDialog>,
    );
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
  });
});

describe("toasts", () => {
  function Pusher({ tone }: { tone: "good" | "error" }) {
    const toast = useToast();
    return (
      <button type="button" onClick={() => toast.push(tone, `A ${tone} thing happened`)}>
        push
      </button>
    );
  }

  it("shows a message and takes it away again", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(
      <ToastProvider>
        <Pusher tone="good" />
      </ToastProvider>,
    );
    await act(async () => screen.getByRole("button", { name: "push" }).click());
    expect(screen.getByText("A good thing happened")).toBeInTheDocument();
    await act(async () => void vi.advanceTimersByTime(4600));
    expect(screen.queryByText("A good thing happened")).not.toBeInTheDocument();
  });

  it("keeps an error on screen longer", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(
      <ToastProvider>
        <Pusher tone="error" />
      </ToastProvider>,
    );
    await act(async () => screen.getByRole("button", { name: "push" }).click());
    await act(async () => void vi.advanceTimersByTime(5000));
    expect(screen.getByText("A error thing happened")).toBeInTheDocument();
    await act(async () => void vi.advanceTimersByTime(4500));
    expect(screen.queryByText("A error thing happened")).not.toBeInTheDocument();
  });

  it("must be used inside its provider", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    expect(() => render(<Pusher tone="good" />)).toThrow("useToast must be used inside <ToastProvider>");
    spy.mockRestore();
  });
});

describe("CopyButton", () => {
  it("copies the text and says so", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<CopyButton text="agentlab serve" />);
    await userEvent.click(screen.getByRole("button", { name: /copy/i }));
    expect(writeText).toHaveBeenCalledWith("agentlab serve");
    expect(await screen.findByText("Copied")).toBeInTheDocument();
  });

  it("does not claim to have copied when the browser refused", async () => {
    Object.defineProperty(navigator, "clipboard", { value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) }, configurable: true });
    render(<CopyButton text="x" />);
    await userEvent.click(screen.getByRole("button", { name: /copy/i }));
    expect(screen.queryByText("Copied")).not.toBeInTheDocument();
  });
});
