import { parseBoolean } from "./parseBoolean.js";

for (const value of ["true", "TRUE", "yes", "1"]) {
  if (parseBoolean(value) !== true) throw new Error(`${value} must be true`);
}
for (const value of ["false", "FALSE", "no", "0"]) {
  if (parseBoolean(value) !== false) throw new Error(`${value} must be false`);
}
try {
  parseBoolean("maybe");
  throw new Error("unknown value must throw TypeError");
} catch (error) {
  if (!(error instanceof TypeError)) throw error;
}
