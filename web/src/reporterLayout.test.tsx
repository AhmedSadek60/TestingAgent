import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("the frame of a person who has no sidebar", () => {
  it("has one column: the grid of the application frame has two, for a sidebar and the page", () => {
    // jsdom does not lay anything out, so the rule is read from the stylesheet itself (the tests run in web/). A frame
    // with only a page in it and the two columns puts the page into the first, the sidebar's: 244px, on every screen
    // wider than 920px.
    const styles = readFileSync("src/styles.css", "utf-8");
    expect(styles.length).toBeGreaterThan(1000);
    const frame = /\.app\s*\{([^}]*)\}/.exec(styles)?.[1] ?? "";
    expect(frame).toMatch(/grid-template-columns:\s*var\(--sidebar\)\s+minmax\(0,\s*1fr\)/);
    const single = /\.app\.app-single\s*\{([^}]*)\}/.exec(styles)?.[1] ?? "";
    expect(single).toMatch(/grid-template-columns:\s*minmax\(0,\s*1fr\)\s*;/);
  });
});
