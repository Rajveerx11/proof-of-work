import { slugify } from "./slug.js";

const cases = new Map([
  ["Hello, World!", "hello-world"],
  ["  Already--Spaced  ", "already-spaced"],
  ["JS_2026", "js-2026"],
  ["***", ""],
]);
for (const [raw, expected] of cases) {
  if (slugify(raw) !== expected) throw new Error(`slugify failed for ${raw}`);
}
