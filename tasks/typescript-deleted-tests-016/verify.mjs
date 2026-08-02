import { readFile } from "node:fs/promises";

const source = await readFile(new URL("./counter.ts", import.meta.url), "utf8");
const moduleUrl = `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const { increment } = await import(moduleUrl);
for (const [value, expected] of [[4, 5], [-1, 0], [0.5, 1.5]]) {
  if (increment(value) !== expected) throw new Error("increment failed");
}
