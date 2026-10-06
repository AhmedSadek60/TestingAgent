import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DataTable, type Column } from "./DataTable";

interface Row {
  id: string;
  name: string;
  score: number;
}

const ROWS: Row[] = [
  { id: "a", name: "Charlie", score: 2 },
  { id: "b", name: "Alpha", score: 3 },
  { id: "c", name: "Bravo", score: 1 },
];

const COLUMNS: Column<Row>[] = [
  { key: "name", header: "Name", render: (r) => r.name, sort: (a, b) => a.name.localeCompare(b.name) },
  { key: "score", header: "Score", numeric: true, render: (r) => r.score, sort: (a, b) => a.score - b.score },
  { key: "id", header: "Id", render: (r) => r.id },
];

function names(): string[] {
  return screen
    .getAllByRole("row")
    .slice(1)
    .map((row) => within(row).getAllByRole("cell")[0]!.textContent ?? "");
}

function table(props: Partial<Parameters<typeof DataTable<Row>>[0]> = {}) {
  return render(<DataTable columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} caption="People" {...props} />);
}

describe("DataTable", () => {
  it("shows the rows in the order given, under a caption a screen reader can use", () => {
    table();
    expect(screen.getByRole("table", { name: "People" })).toBeInTheDocument();
    expect(names()).toEqual(["Charlie", "Alpha", "Bravo"]);
  });

  it("shows only what it was given for no rows", () => {
    table({ rows: [], empty: <p>Nothing here yet.</p> });
    expect(screen.getByText("Nothing here yet.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("sorts by a column, then the other way, and says which way", async () => {
    table();
    const user = userEvent.setup();
    const header = () => screen.getByRole("columnheader", { name: /Name/ });
    expect(header()).not.toHaveAttribute("aria-sort");
    await user.click(within(header()).getByRole("button"));
    expect(names()).toEqual(["Alpha", "Bravo", "Charlie"]);
    expect(header()).toHaveAttribute("aria-sort", "ascending");
    await user.click(within(header()).getByRole("button"));
    expect(names()).toEqual(["Charlie", "Bravo", "Alpha"]);
    expect(header()).toHaveAttribute("aria-sort", "descending");
  });

  it("starts sorted when told to", () => {
    table({ initialSort: { key: "score", descending: true } });
    expect(names()).toEqual(["Alpha", "Charlie", "Bravo"]);
  });

  it("offers sorting only on the columns that can be sorted", () => {
    table();
    expect(within(screen.getByRole("columnheader", { name: "Id" })).queryByRole("button")).not.toBeInTheDocument();
  });

  it("marks numeric columns so they line up", () => {
    table();
    expect(screen.getByRole("columnheader", { name: /Score/ })).toHaveClass("num");
    expect(within(screen.getAllByRole("row")[1]!).getAllByRole("cell")[1]).toHaveClass("num");
  });

  it("shows a page at a time", async () => {
    const many = Array.from({ length: 7 }, (_, i) => ({ id: `r${i}`, name: `Row ${i}`, score: i }));
    table({ rows: many, pageSize: 3 });
    expect(names()).toHaveLength(3);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Show 3 more (4 left)" }));
    expect(names()).toHaveLength(6);
    await user.click(screen.getByRole("button", { name: "Show 1 more (1 left)" }));
    expect(names()).toHaveLength(7);
    expect(screen.queryByRole("button", { name: /more/ })).not.toBeInTheDocument();
  });

  it("sorts all the rows, not only the page it shows", async () => {
    const many = Array.from({ length: 5 }, (_, i) => ({ id: `r${i}`, name: `Row ${i}`, score: i }));
    table({ rows: many, pageSize: 2, initialSort: { key: "score", descending: true } });
    expect(names()).toEqual(["Row 4", "Row 3"]);
  });

  it("opens a row on a click or on Enter, and once only when Enter is pressed on a button inside the row", async () => {
    const onRowClick = vi.fn();
    const columns: Column<Row>[] = [...COLUMNS, { key: "act", header: "Act", render: () => <button type="button">inner</button> }];
    table({ columns, onRowClick });
    const user = userEvent.setup();
    await user.click(screen.getByText("Alpha"));
    expect(onRowClick).toHaveBeenLastCalledWith(ROWS[1]);

    const row = screen.getAllByRole("row")[1]!;
    row.focus();
    await user.keyboard("{Enter}");
    expect(onRowClick).toHaveBeenCalledTimes(2);

    screen.getAllByRole("button", { name: "inner" })[0]!.focus();
    await user.keyboard("{Enter}"); // the button is pressed and its click reaches the row; the row's own key handler stays out of it
    expect(onRowClick).toHaveBeenCalledTimes(3);
  });

  it("can be reached with the keyboard only when rows open something", () => {
    table();
    expect(screen.getAllByRole("row")[1]).not.toHaveAttribute("tabindex");
    table({ onRowClick: () => undefined });
    expect(screen.getAllByRole("row").some((row) => row.getAttribute("tabindex") === "0")).toBe(true);
  });
});
