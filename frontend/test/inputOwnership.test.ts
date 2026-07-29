import assert from 'node:assert/strict'
import test from 'node:test'
import {
  applyOwnerFrame,
  applyOwnerReleased,
  applyRejectedFrame,
  claimReasonForFocus,
  GESTURE_WINDOW_MS,
  inputOwnerNotice,
  shouldReclaimAfterDisplacement,
  shouldReplayRejectedInput,
  UNOWNED,
} from '../src/inputOwnership.ts'

test('an ownership grant is applied and clears any earlier refusal', () => {
  const denied = applyOwnerFrame(UNOWNED, { active: false, epoch: 3, reason: 'denied_active_owner', owner_device: 'mobile' })
  assert.deepEqual(denied, { owns: false, epoch: 3, ownerDevice: 'mobile', denied: 'denied_active_owner' })
  const granted = applyOwnerFrame(denied, { active: true, epoch: 4, reason: 'granted_gesture', owner_device: 'desktop' })
  assert.deepEqual(granted, { owns: true, epoch: 4, ownerDevice: 'desktop', denied: null })
})

test('an ownership frame that lost a race with a newer transfer is discarded', () => {
  // Two devices claiming at once means two notifications in flight over links of
  // different latency. Applying the older one would resurrect a dead world.
  const current = applyOwnerFrame(UNOWNED, { active: true, epoch: 7, owner_device: 'mobile' })
  const stale = applyOwnerFrame(current, { active: false, epoch: 6, reason: 'claimed_elsewhere', owner_device: 'desktop' })
  assert.equal(stale, current)
  const fresh = applyOwnerFrame(current, { active: false, epoch: 8, reason: 'claimed_elsewhere', owner_device: 'desktop' })
  assert.deepEqual(fresh, { owns: false, epoch: 8, ownerDevice: 'desktop', denied: 'claimed_elsewhere' })
})

test('a rejection proves the pane no longer owns input', () => {
  const owned = applyOwnerFrame(UNOWNED, { active: true, epoch: 2, owner_device: 'mobile' })
  const rejected = applyRejectedFrame(owned, { epoch: 3, owner_device: 'desktop', data: 'x' })
  assert.deepEqual(rejected, { owns: false, epoch: 3, ownerDevice: 'desktop', denied: 'input_rejected' })
})

test('a release only clears a pane that does not already own input', () => {
  const spectator = applyOwnerFrame(UNOWNED, { active: false, epoch: 4, reason: 'claimed_elsewhere', owner_device: 'mobile' })
  assert.deepEqual(applyOwnerReleased(spectator, 4), { owns: false, epoch: 4, ownerDevice: null, denied: null })
  const owner = applyOwnerFrame(UNOWNED, { active: true, epoch: 5, owner_device: 'desktop' })
  assert.equal(applyOwnerReleased(owner, 5), owner)
  assert.equal(applyOwnerReleased(spectator, 1), spectator)
})

test('only a visible, focused pane takes input back on its own', () => {
  // The bug: `document.activeElement` stays inside the terminal of a minimized
  // window, so focus alone had a background desktop pane stealing the keyboard back
  // from the phone on every claim, forever.
  const base = {
    reason: 'claimed_elsewhere', focusInHost: true, documentHidden: false,
    windowFocused: true, lastReclaimAt: null, now: 10_000,
  }
  assert.equal(shouldReclaimAfterDisplacement(base), true)
  assert.equal(shouldReclaimAfterDisplacement({ ...base, windowFocused: false }), false)
  assert.equal(shouldReclaimAfterDisplacement({ ...base, documentHidden: true }), false)
  assert.equal(shouldReclaimAfterDisplacement({ ...base, focusInHost: false }), false)
})

test('a refusal is never grounds to claim again', () => {
  // A refusal and a displacement are both `input_owner` with `active:false`. Acting
  // on the refusal is a claim/deny loop that runs at the speed of the round trip —
  // one live session had logged 7566 refused claims that way.
  const base = {
    focusInHost: true, documentHidden: false, windowFocused: true,
    lastReclaimAt: null, now: 10_000,
  }
  assert.equal(shouldReclaimAfterDisplacement({ ...base, reason: 'denied_device_in_use' }), false)
  assert.equal(shouldReclaimAfterDisplacement({ ...base, reason: 'denied_active_owner' }), false)
  assert.equal(shouldReclaimAfterDisplacement({ ...base, reason: 'denied_unfocused' }), false)
  assert.equal(shouldReclaimAfterDisplacement({ ...base, reason: null }), false)
})

test('a pane cannot re-claim twice inside the cooldown', () => {
  const base = {
    reason: 'claimed_elsewhere', focusInHost: true, documentHidden: false,
    windowFocused: true, now: 10_000,
  }
  assert.equal(shouldReclaimAfterDisplacement({ ...base, lastReclaimAt: 9_000 }), false)
  assert.equal(shouldReclaimAfterDisplacement({ ...base, lastReclaimAt: 4_999 }), true)
})

test('focus counts as a gesture only just after a real interaction', () => {
  assert.equal(claimReasonForFocus(null, 10_000), 'passive')
  assert.equal(claimReasonForFocus(10_000, 10_000 + GESTURE_WINDOW_MS), 'gesture')
  assert.equal(claimReasonForFocus(10_000, 10_001 + GESTURE_WINDOW_MS), 'passive')
})

test('rejected keystrokes are replayed once and never in a loop', () => {
  assert.equal(shouldReplayRejectedInput({ data: 'hi' }), true)
  assert.equal(shouldReplayRejectedInput({ data: 'hi', retry: true }), false)
  assert.equal(shouldReplayRejectedInput({ data: '' }), false)
  assert.equal(shouldReplayRejectedInput({}), false)
})

test('a non-owning pane says which device holds the keyboard', () => {
  assert.equal(inputOwnerNotice(UNOWNED), null)
  assert.equal(inputOwnerNotice({ owns: true, epoch: 1, ownerDevice: 'desktop', denied: null }), null)
  assert.equal(
    inputOwnerNotice({ owns: false, epoch: 1, ownerDevice: 'mobile', denied: 'denied_active_owner' }),
    'Input active on mobile',
  )
})
