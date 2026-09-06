/** A small invented clipboard image, delivered through the app's native paste handler. */
export async function pasteExampleImage(continueRun: () => boolean): Promise<void> {
  const canvas = document.createElement('canvas')
  canvas.width = 480; canvas.height = 240
  const context = canvas.getContext('2d')
  if (!context) throw new Error('The browser could not prepare the example image.')
  context.fillStyle = '#f7f8fa'; context.fillRect(0, 0, 480, 240)
  context.fillStyle = '#202632'; context.font = '24px sans-serif'
  context.fillText('Checkout', 24, 48)
  context.font = '17px sans-serif'
  context.fillText('Order total: $24.00', 24, 96)
  context.fillStyle = '#ae243c'; context.fillText('Coupon expired', 24, 140)
  context.fillStyle = '#486327'; context.fillRect(24, 170, 160, 42)
  context.fillStyle = '#ffffff'; context.fillText('Place order', 42, 198)
  const blob = await new Promise<Blob>((done, fail) => canvas.toBlob(
    result => result ? done(result) : fail(new Error('Could not encode the example image.')), 'image/png',
  ))
  if (!continueRun()) return
  const transfer = new DataTransfer()
  transfer.items.add(new File([blob], 'cart.png', { type: 'image/png' }))
  const input = document.querySelector<HTMLElement>('.terminal-pane.focused .xterm-helper-textarea')
  if (!input) throw new Error('Select an agent pane before trying image paste.')
  input.focus()
  input.dispatchEvent(new ClipboardEvent('paste', { bubbles: true, cancelable: true, clipboardData: transfer }))
}
