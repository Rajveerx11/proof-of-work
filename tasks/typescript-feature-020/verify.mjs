import { readFile } from "node:fs/promises";

const source = await readFile(new URL("./format.ts", import.meta.url), "utf8");
const moduleUrl = `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const { initials } = await import(moduleUrl);
for (const [name, expected] of [["Ada Lovelace", "AL"], ["  grace   hopper ", "GH"], ["", ""]]) {
  if (initials(name) !== expected) throw new Error(`initials failed for ${name}`);
}
