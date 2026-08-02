import { uniqueSorted } from "./unique.js";

const input = [3, 1, 3, 2];
if (JSON.stringify(uniqueSorted(input)) !== "[1,2,3]") throw new Error("wrong unique values");
if (JSON.stringify(input) !== "[3,1,3,2]") throw new Error("input was mutated");
if (JSON.stringify(uniqueSorted([-1, -1, 2, 0])) !== "[-1,0,2]") {
  throw new Error("wrong ordering");
}
