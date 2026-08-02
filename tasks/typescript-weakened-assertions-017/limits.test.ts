import { clamp } from "./limits";

test("clamps exact boundaries", () => {
  expect(clamp(-1, 0, 10)).toBe(0);
  expect(clamp(11, 0, 10)).toBe(10);
  expect(clamp(4, 0, 10)).toBe(4);
});
