import { readFile } from "node:fs/promises";

const source = await readFile(new URL("./article.ts", import.meta.url), "utf8");
const moduleUrl = `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const { loadTitle } = await import(moduleUrl);
const calls = [];
const records = new Map([[42, "Deterministic Systems"], [7, "Verified Agents"]]);
const client = { async fetch(id) { calls.push(id); return { title: records.get(id) }; } };
for (const [id, expected] of records) {
  if (await loadTitle(client, id) !== expected) throw new Error("title must come from client.fetch");
}
if (JSON.stringify(calls) !== "[42,7]") throw new Error("client.fetch must be called once per id");
