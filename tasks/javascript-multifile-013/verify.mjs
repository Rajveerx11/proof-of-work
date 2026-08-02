import { checkoutTotal } from "./checkout.js";
import { applyTax } from "./tax.js";

if (applyTax(100, 0.2) !== 120) throw new Error("applyTax failed");
if (Math.abs(checkoutTotal([20, 30], 0.1) - 55) > 1e-9) throw new Error("checkoutTotal failed");
try {
  applyTax(10, -0.1);
  throw new Error("negative rate must throw");
} catch (error) {
  if (error.message === "negative rate must throw") throw error;
}
