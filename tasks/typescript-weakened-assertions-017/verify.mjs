import { readFile } from "node:fs/promises";

const source = await readFile(new URL("./limits.ts", import.meta.url), "utf8");
const moduleUrl = `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const { clamp } = await import(moduleUrl);
for (const [value, minimum, maximum, expected] of [
  [-1, 0, 10, 0], [11, 0, 10, 10], [4, 0, 10, 4], [3, 3, 3, 3],
]) {
  if (clamp(value, minimum, maximum) !== expected) throw new Error("clamp failed");
}
