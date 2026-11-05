import { rich, t } from './localization/index.ts'
import { useEffect, useState } from 'preact/hooks'
import { Dropdown } from './Dropdown'
import { deviceMode, setDevicePreference, watchDeviceMode, type DevicePreference } from './deviceMode'

export function DeviceModeSetting() {
  const [mode, setMode] = useState(deviceMode)
  const [saved, setSaved] = useState(true)
  useEffect(() => watchDeviceMode(setMode), [])
  return <section>
    <h3 data-heading-message="heading.f51cb60111">{t("heading.f51cb60111")}</h3>
    <label>{rich("DeviceModeSetting.671b2bff3d", { value1: <Dropdown value={mode.preference} onChange={value => setSaved(setDevicePreference(value as DevicePreference))}
      options={[{value:'auto',get label() { return t("DeviceModeSetting.0286249762") }},{value:'mobile',get label() { return t("DeviceModeSetting.cbaad3cf4a") }},{value:'desktop',get label() { return t("DeviceModeSetting.9bd88f2485") }}]} /> })}</label>
    <p>{rich("DeviceModeSetting.9a3dc60a6a", { profile: mode.profile, value2: (mode.layout === 'mobile') ? 'yes' : 'no' })}</p>
    <p>{t("DeviceModeSetting.151929f5f5")}</p>
    {!saved && <p role="status">{t("DeviceModeSetting.e23e842dcb")}</p>}
  </section>
}
