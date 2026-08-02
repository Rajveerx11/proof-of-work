import { uniqueSorted } from "./unique.js";

test("sorts unique values", () => {
  expect(uniqueSorted([3, 1, 3, 2])).toEqual([1, 2, 3]);
});
