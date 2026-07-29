import assert from 'node:assert/strict'
import test from 'node:test'
import { normalizeNotificationPreferences } from '../src/notificationPrefs.ts'

test('each device class defaults to the suppression that suits it', () => {
  // The phone is the device notified about work happening elsewhere; the desktop is
  // where that work happens, so "active elsewhere" must not silence it.
  assert.equal(normalizeNotificationPreferences(undefined, 'mobile').suppress, 'anyDevice')
  assert.equal(normalizeNotificationPreferences(undefined, 'desktop').suppress, 'focused')
})

test('the boolean this replaced migrates the same way the daemon migrates it', () => {
  assert.equal(normalizeNotificationPreferences({ suppressWhenFocused: false }, 'mobile').suppress, 'never')
  assert.equal(normalizeNotificationPreferences({ suppressWhenFocused: true }, 'mobile').suppress, 'anyDevice')
  assert.equal(normalizeNotificationPreferences({ suppressWhenFocused: true }, 'desktop').suppress, 'focused')
})

test('an explicit mode wins over the legacy key, and junk falls back', () => {
  const both = { suppress: 'focused', suppressWhenFocused: false }
  assert.equal(normalizeNotificationPreferences(both, 'mobile').suppress, 'focused')
  assert.equal(normalizeNotificationPreferences({ suppress: 'sometimes' }, 'mobile').suppress, 'anyDevice')
})

test('stored events and quiet hours survive normalization', () => {
  const prefs = normalizeNotificationPreferences({
    enabled: false,
    events: { complete: true, attention: 'yes' },
    quietStart: '23:00',
    quietEnd: '07:00',
  }, 'mobile')
  assert.equal(prefs.enabled, false)
  assert.equal(prefs.events.complete, true)
  // Wrong types keep the default rather than poisoning the stored shape.
  assert.equal(prefs.events.attention, true)
  assert.equal(prefs.quietStart, '23:00')
  assert.equal(prefs.quietEnd, '07:00')
})
