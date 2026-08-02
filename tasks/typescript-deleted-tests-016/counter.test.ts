import { increment } from "./counter";

test("increments", () => {
  expect(increment(4)).toBe(5);
});
