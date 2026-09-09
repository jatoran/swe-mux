import { useEffect, useState } from 'preact/hooks'
import { Dropdown } from './Dropdown'
import { deviceMode, setDevicePreference, watchDeviceMode, type DevicePreference } from './deviceMode'

export function DeviceModeSetting() {
  const [mode, setMode] = useState(deviceMode)
  const [saved, setSaved] = useState(true)
  useEffect(() => watchDeviceMode(setMode), [])
  return <section>
    <h3>Device mode</h3>
    <label>This browser<Dropdown value={mode.preference} onChange={value => setSaved(setDevicePreference(value as DevicePreference))}
      options={[{value:'auto',label:'Auto'},{value:'mobile',label:'Mobile'},{value:'desktop',label:'Desktop'}]} /></label>
    <p>Using {mode.profile} settings and {mode.layout === 'mobile' ? 'mobile navigation' : 'desktop layout'}.
      Auto keeps phones and tablets on mobile controls when their screens widen.</p>
    <p>Applies immediately and is saved only in this browser. Other devices keep their own mode.</p>
    {!saved && <p role="status">Browser storage is unavailable. This choice lasts until the page closes.</p>}
  </section>
}
