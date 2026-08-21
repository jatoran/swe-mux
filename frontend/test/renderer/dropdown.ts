import { expect, type Locator, type Page } from 'playwright/test'

/**
 * Playwright's `selectOption` for the app's custom dropdown (`Dropdown.tsx`).
 *
 * The rows are portalled to `document.body`, so they are never inside the trigger's own
 * container and a spec cannot reach them from the locator it used to open the list. This is
 * the one place that knows it, which is why every spec calls through here rather than
 * hand-rolling a `.dropdown-list` locator that would have to be corrected in twelve files if
 * the portal target ever moved.
 */
export async function chooseDropdown(page: Page, trigger: Locator, value: string): Promise<void> {
  await trigger.click()
  const list = page.locator('.dropdown-list')
  await expect(list).toHaveCount(1)
  await list.locator(`.dropdown-option[data-value="${value}"]`).click()
  await expect(list).toHaveCount(0)
}

/** The collapsed value of a dropdown, as `toHaveValue` would have read a `<select>`. */
export const dropdownValue = (trigger: Locator): Promise<string | null> =>
  trigger.getAttribute('data-value')
