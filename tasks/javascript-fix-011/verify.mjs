import { sum } from "./math.js";

for (const [left, right, expected] of [[2, 3, 5], [-2, 4, 2], [0.5, 0.25, 0.75]]) {
  if (sum(left, right) !== expected) throw new Error("sum returned wrong result");
}
