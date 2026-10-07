import { useMemo, useState, type ReactNode } from "react";

export interface Column<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  /** Makes the column sortable: negative when `a` comes first. */
  sort?: (a: T, b: T) => number;
  numeric?: boolean;
  width?: string;
}

/** A table that sorts, and shows a page at a time. It holds no data of its own: filtering belongs to the screen. */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
  initialSort,
  pageSize = 50,
  caption,
  empty,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  initialSort?: { key: string; descending?: boolean };
  pageSize?: number;
  caption: string;
  empty?: ReactNode;
}) {
  const [sort, setSort] = useState(initialSort ?? null);
  const [shown, setShown] = useState(pageSize);
  const sorted = useMemo(() => {
    const column = columns.find((c) => c.key === sort?.key);
    if (!column?.sort) return rows;
    const direction = sort?.descending ? -1 : 1;
    return [...rows].sort((a, b) => direction * column.sort!(a, b));
  }, [rows, columns, sort]);
  if (rows.length === 0) return <>{empty}</>;
  return (
    <div className="table-wrap">
      <table className="table">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={column.numeric ? "num" : undefined}
                style={column.width ? { width: column.width } : undefined}
                aria-sort={sort?.key === column.key ? (sort.descending ? "descending" : "ascending") : undefined}
              >
                {column.sort ? (
                  <button type="button" onClick={() => setSort(sort?.key === column.key ? { key: column.key, descending: !sort.descending } : { key: column.key })}>
                    {column.header}
                    {sort?.key === column.key ? (sort.descending ? " ↓" : " ↑") : ""}
                  </button>
                ) : (
                  column.header
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.slice(0, shown).map((row) => (
            <tr
              key={rowKey(row)}
              className={onRowClick ? "clickable" : undefined}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              onKeyDown={
                onRowClick
                  ? (event) => {
                      if (event.key === "Enter" && event.target === event.currentTarget) onRowClick(row);
                    }
                  : undefined
              }
              tabIndex={onRowClick ? 0 : undefined}
            >
              {columns.map((column) => (
                <td key={column.key} className={column.numeric ? "num" : undefined}>
                  {column.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {sorted.length > shown && (
        <div className="row" style={{ padding: 12, justifyContent: "center" }}>
          <button type="button" className="btn" onClick={() => setShown((n) => n + pageSize)}>
            Show {Math.min(pageSize, sorted.length - shown)} more ({sorted.length - shown} left)
          </button>
        </div>
      )}
    </div>
  );
}
