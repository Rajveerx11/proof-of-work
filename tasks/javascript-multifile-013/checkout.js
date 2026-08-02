import { applyTax } from "./tax.js";

export function checkoutTotal(items, rate) {
  return items.reduce((total, item) => total + item, 0);
}
