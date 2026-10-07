import { describe, expect, it } from "vitest";
import {
  formatCost,
  formatDuration,
  formatLatency,
  formatNumber,
  formatPercent,
  formatScore,
  formatTime,
  parseTime,
  plural,
  pretty,
  relativeTime,
  secondsBetween,
  stripFrontMatter,
  titleCase,
  truncate,
} from "./format";

describe("numbers", () => {
  it("writes money with the precision a small amount needs", () => {
    expect(formatCost(0)).toBe("$0");
    expect(formatCost(0.0018)).toBe("$0.0018");
    expect(formatCost(0.05)).toBe("$0.050");
    expect(formatCost(12.5)).toBe("$12.50");
    expect(formatCost(null)).toBe("n/a");
    expect(formatCost(Number.NaN)).toBe("n/a");
  });

  it("groups thousands and says n/a for what is missing", () => {
    expect(formatNumber(1234567)).toBe("1,234,567");
    expect(formatNumber(undefined)).toBe("n/a");
  });

  it("shows scores without a pointless decimal", () => {
    expect(formatScore(40)).toBe("40");
    expect(formatScore(84.25)).toBe("84.3");
    expect(formatScore(null)).toBe("n/a");
  });

  it("turns fractions into percentages", () => {
    expect(formatPercent(0.73)).toBe("73%");
    expect(formatPercent(0.7351, 1)).toBe("73.5%");
    expect(formatPercent(null)).toBe("n/a");
  });
});

describe("durations", () => {
  it("picks the unit a reader wants", () => {
    expect(formatDuration(0.562)).toBe("562 ms");
    expect(formatDuration(1.3)).toBe("1.3 s");
    expect(formatDuration(42)).toBe("42 s");
    expect(formatDuration(81)).toBe("1 min 21 s");
    expect(formatDuration(120)).toBe("2 min");
    expect(formatDuration(3600)).toBe("1 h 0 min");
    expect(formatDuration(5400)).toBe("1 h 30 min");
    expect(formatDuration(null)).toBe("n/a");
  });

  it("never shows a negative time, which a clock a little behind the server's would give", () => {
    expect(formatDuration(-0.004)).toBe("0 ms");
    expect(formatDuration(-30)).toBe("0 ms");
  });

  it("writes latency in milliseconds, then seconds", () => {
    expect(formatLatency(5)).toBe("5 ms");
    expect(formatLatency(1500)).toBe("1.50 s");
    expect(formatLatency(12000)).toBe("12.0 s");
    expect(formatLatency(null)).toBe("n/a");
  });
});

describe("times", () => {
  it("reads a stored UTC time with or without its offset", () => {
    expect(parseTime("2026-10-06T10:00:00Z")?.toISOString()).toBe("2026-10-06T10:00:00.000Z");
    expect(parseTime("2026-10-06T10:00:00")?.toISOString()).toBe("2026-10-06T10:00:00.000Z");
    expect(parseTime("2026-10-06T12:00:00+02:00")?.toISOString()).toBe("2026-10-06T10:00:00.000Z");
    expect(parseTime("not a time")).toBeNull();
    expect(parseTime(null)).toBeNull();
    expect(formatTime(undefined)).toBe("n/a");
  });

  it("says how long ago something was", () => {
    const now = new Date("2026-10-06T12:00:00Z");
    expect(relativeTime("2026-10-06T11:59:58Z", now)).toBe("just now");
    expect(relativeTime("2026-10-06T11:59:30Z", now)).toBe("30 s ago");
    expect(relativeTime("2026-10-06T11:45:00Z", now)).toBe("15 min ago");
    expect(relativeTime("2026-10-06T07:00:00Z", now)).toBe("5 h ago");
    expect(relativeTime("2026-10-01T12:00:00Z", now)).toBe("5 days ago");
    expect(relativeTime("2026-10-06T13:00:00Z", now)).toBe("in the future");
  });

  it("measures between two stored times, using now for a run that has not ended", () => {
    const now = new Date("2026-10-06T10:00:30Z");
    expect(secondsBetween("2026-10-06T10:00:00Z", "2026-10-06T10:00:10Z", now)).toBe(10);
    expect(secondsBetween("2026-10-06T10:00:00Z", null, now)).toBe(30);
    expect(secondsBetween("2026-10-06T10:01:00Z", null, now)).toBe(0);
    expect(secondsBetween(null, null, now)).toBeNull();
  });
});

describe("text", () => {
  it("makes identifiers readable", () => {
    expect(titleCase("stopped_due_to_cost")).toBe("Stopped due to cost");
    expect(titleCase("")).toBe("");
    expect(plural(1, "test")).toBe("1 test");
    expect(plural(1200, "test")).toBe("1,200 tests");
  });

  it("truncates with an ellipsis and keeps short text whole", () => {
    expect(truncate("abcdef", 10)).toBe("abcdef");
    expect(truncate("abcdef", 4)).toBe("abc…");
  });

  it("serialises anything for display without throwing", () => {
    expect(pretty("plain")).toBe("plain");
    expect(pretty({ a: 1 })).toBe('{\n  "a": 1\n}');
    const loop: Record<string, unknown> = {};
    loop.self = loop;
    expect(pretty(loop)).toBe("[object Object]");
  });

  it("drops the header a skill file starts with, and nothing else", () => {
    expect(stripFrontMatter("---\nname: x\ndescription: y\n---\n\n# Title\n\nBody")).toBe("# Title\n\nBody");
    expect(stripFrontMatter("---\r\nname: x\r\n---\r\nBody")).toBe("Body");
    expect(stripFrontMatter("# No header\n---\nnot front matter\n---")).toBe("# No header\n---\nnot front matter\n---");
    expect(stripFrontMatter("")).toBe("");
  });
});
