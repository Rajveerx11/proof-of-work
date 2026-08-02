import { inclusiveRange } from "./range.js";

const cases = [
  [2, 4, [2, 3, 4]],
  [3, 3, [3]],
  [2, -1, [2, 1, 0, -1]],
];
for (const [start, end, expected] of cases) {
  if (JSON.stringify(inclusiveRange(start, end)) !== JSON.stringify(expected)) {
    throw new Error("inclusiveRange failed");
  }
}
