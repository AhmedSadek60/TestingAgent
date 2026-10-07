import { titleCase } from "../lib/format";

/** Category scores as a radar chart. Drawn by hand: no chart library is worth shipping for one picture. */
export function Radar({ items, size = 320 }: { items: { label: string; value: number | null }[]; size?: number }) {
  const scored = items.filter((i) => i.value !== null);
  if (scored.length < 3) return null; // fewer than three axes is not a shape
  const center = size / 2;
  const radius = center - 52;
  const margin = 70; // room for the labels either side of the picture
  const angle = (index: number) => (Math.PI * 2 * index) / scored.length - Math.PI / 2;
  const point = (index: number, fraction: number): [number, number] => [
    center + Math.cos(angle(index)) * radius * fraction,
    center + Math.sin(angle(index)) * radius * fraction,
  ];
  const polygon = scored.map((item, index) => point(index, Math.max(0, Math.min(1, (item.value ?? 0) / 100))).join(",")).join(" ");
  const summary = scored.map((i) => `${i.label} ${Math.round(i.value ?? 0)}`).join(", ");
  return (
    <svg width="100%" viewBox={`${-margin} 0 ${size + 2 * margin} ${size}`} style={{ maxWidth: size + 2 * margin }} role="img" aria-label={`Category scores: ${summary}`}>
      {[0.25, 0.5, 0.75, 1].map((ring) => (
        <polygon key={ring} points={scored.map((_, i) => point(i, ring).join(",")).join(" ")} fill="none" stroke="var(--border)" />
      ))}
      {scored.map((_, i) => {
        const [x, y] = point(i, 1);
        return <line key={i} x1={center} y1={center} x2={x} y2={y} stroke="var(--border)" />;
      })}
      <polygon points={polygon} fill="var(--accent)" fillOpacity="0.22" stroke="var(--accent)" strokeWidth="2" />
      {scored.map((item, i) => {
        const [x, y] = point(i, 1.14);
        const anchor = Math.abs(x - center) < 6 ? "middle" : x > center ? "start" : "end";
        return (
          <text key={item.label} x={x} y={y} fontSize="10.5" fill="var(--muted)" textAnchor={anchor} dominantBaseline="middle">
            {shortLabel(item.label)}
          </text>
        );
      })}
    </svg>
  );
}

function shortLabel(label: string): string {
  const text = titleCase(label);
  return text.length > 20 ? `${text.slice(0, 19)}…` : text;
}
