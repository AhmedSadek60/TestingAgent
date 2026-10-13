// The tests run under Node, but @types/node is not installed: only what a test reads from disk is declared here.
declare module "node:fs" {
  export function readFileSync(path: string, encoding: "utf-8"): string;
}
