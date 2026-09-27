import { expect, test, type Page } from "@playwright/test";

const SHARMA = "+91 90000 10001";
const GREENLINE = "+91 90000 10011";
const ARORA = "+91 90000 10021";

async function signIn(page: Page, phone: string) {
  await page.goto("/login");
  await page.fill("#phone", phone);
  await page.click("button:has-text('Send code')");
  const otp = await page.locator(".box strong.mono").innerText();
  await page.fill("#code", otp);
  await page.click("button:has-text('Verify')");
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
}

/** Dashboard -> BOM with this title -> its (first) RFQ. */
async function openRfq(page: Page, title: string) {
  await page.getByRole("row", { name: title }).getByRole("link").first().click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText(title);
  await page.locator("a.mono[href^='/rfqs/']").first().click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("RFQ-");
}

test("happy path: approve L1 and a work order is created", async ({ page }) => {
  await signIn(page, SHARMA);
  await openRfq(page, "Scenario 1: Tower A slab");
  await expect(page.getByRole("heading", { name: "Recommendation" })).toBeVisible();
  await page.getByRole("button", { name: "Approve Delhi Cement Depot" }).click();
  const orders = page.locator(".box", { has: page.getByRole("heading", { name: "Work orders" }) });
  await expect(orders).toContainText("Delhi Cement Depot");
  await expect(orders.locator("a.mono")).toHaveCount(1);
});

test("big order: approve the split and one work order per vendor", async ({ page }) => {
  await signIn(page, SHARMA);
  await openRfq(page, "Scenario 2: Podium raft (big order)");
  await expect(page.getByText("Split proposal:")).toBeVisible();
  await page.getByRole("button", { name: "Approve split" }).click();
  const orders = page.locator(".box", { has: page.getByRole("heading", { name: "Work orders" }) });
  await expect(orders.locator("a.mono").nth(1)).toBeVisible();
});

test("capacity conflict: refused, then another vendor is approved", async ({ page }) => {
  await signIn(page, GREENLINE);
  await openRfq(page, "Scenario 4: Tower 2 footing");
  await page.getByRole("button", { name: "Approve Gupta Building Materials" }).click();
  await expect(page.locator(".error")).toContainText("no longer has capacity");
  const vendorId = await page.locator("#other-vendor option", { hasText: "Shree Balaji" }).getAttribute("value");
  await page.selectOption("#other-vendor", vendorId ?? "");
  await page.getByRole("button", { name: "Approve selected" }).click();
  const orders = page.locator(".box", { has: page.getByRole("heading", { name: "Work orders" }) });
  await expect(orders).toContainText("Shree Balaji");
});

test("PDF with hidden instructions is read as data and changes nothing", async ({ page }) => {
  await signIn(page, ARORA);
  await openRfq(page, "Scenario 3: PDF and photo quotes");
  const row = page.getByText("Suspicious content in document").locator("xpath=ancestor::tr[1]");
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("400");
  await expect(row).not.toContainText("500.00");
  await expect(page.getByRole("heading", { level: 1 })).toContainText(/bidding/i);
  await expect(page.getByRole("heading", { name: "Recommendation" })).toHaveCount(0);
});
