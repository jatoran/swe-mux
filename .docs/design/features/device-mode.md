# Device mode

## Policy

The browser resolves interaction profile and workspace layout separately.
Viewport width controls available space and never identifies the physical device.

| Preference | Primary pointer | Width | Settings profile | Workspace layout |
| --- | --- | --- | --- | --- |
| Auto | Coarse, without hover | Any | Mobile | Mobile |
| Auto | Any other combination | At most 760 CSS pixels | Desktop | Mobile |
| Auto | Any other combination | Over 760 CSS pixels | Desktop | Desktop |
| Mobile | Any | Any | Mobile | Mobile |
| Desktop | Any | Any | Desktop | Desktop |

The mobile layout projects the durable pane tree into one selected view and a unified tab rail.
It retains mobile navigation, drawer overlays, gestures, and touch-sized controls on unfolded phones and wide tablets.
Content can use the additional width without enabling desktop splits.
Folding or rotating a touch-primary device never changes its settings profile or selected view merely because the width crosses 760 pixels.
The saved desktop pane tree remains unchanged by projection.

Primary-pointer queries are browser assessments rather than physical device identity.
A secondary touchscreen does not classify a mouse-primary laptop as mobile.
Attaching peripherals can change the browser's assessment; the explicit override handles ambiguous reporting and user preference.
User-agent strings, phone-model lists, and experimental hinge APIs are not required.

## Browser preference

Settings > Appearance > Interface > Device mode exposes Auto, Mobile, and Desktop.
The preference applies immediately, independently of the daemon settings Save/Discard transaction.
It is stored under `mux.device-mode.v1` in this origin's local storage and synchronizes across tabs of that browser.
Other browsers and devices retain their own choice.
Unknown stored values resolve to Auto.
If storage is blocked, the current page still applies the choice and the control reports that it cannot persist it.

The profile selects mobile or desktop settings for scale, rail configuration, session top bars, density, sounds, alerts, and notification presence.
Presence reports immediately when the policy changes, and terminal input claims read the current profile rather than a mount-time copy.

## Input and rendering

Pointer and hover capabilities remain independent of the chosen layout.
Soft-keyboard and terminal IME affordances follow the primary coarse pointer, including with the Desktop override.
The override never fabricates a physical keyboard or changes a pointer event's type.
Keyboard reservation CSS applies on both mobile layouts and coarse-pointer devices.
Mobile workspace terminals use the existing DOM-renderer policy and responsive width envelope.

The shared policy writes `data-workspace-layout`, `data-device-profile`, and `data-touch-input` on the document root before rendering.
Semantic mobile chrome rules consume those attributes with zero additional selector specificity.
Content-fitting queries remain viewport-based where they describe available space, such as analytics chart columns and panel breakpoints.
Consumers subscribe to `mux:device-mode-changed`; profile-dependent settings consumers also receive `mux:settings-changed`.
Media-query changes, browser history restoration, and cross-tab preference changes refresh the policy without a reload.

## Diagnostics and verification

`mux.device-mode-diagnostics.v1` stores the latest 64 browser-local initialization, capability, and preference transition records.
Records carry an ISO timestamp, severity, component, operation, page identifier, sequence, resolved policy, and viewport dimensions.
The bounded ring rolls over as new records arrive; records contain no terminal or draft content.
Storage failures produce console warnings and do not interrupt interaction.

`frontend/test/deviceMode.test.ts` verifies policy resolution and invalid preferences.
`frontend/test/renderer/device-mode.spec.ts` exercises wide touch layouts, live fold/rotation resizing, mounted-terminal retention, real touch gestures, keyboard reservation, desktop resizing, browser preference persistence, and tab synchronization.
The renderer suite emulates browser capabilities; physical hinge and software-keyboard behavior still depend on the device's browser.

## Key files

- `frontend/src/deviceMode.ts`: policy, browser preference, subscriptions, root attributes, and bounded diagnostics.
- `frontend/src/DeviceModeSetting.tsx`: immediate browser-local override.
- `frontend/src/deviceSettings.ts`: profile-based daemon settings.
- `frontend/src/App.tsx`: workspace projection and gesture lifecycle.
- `frontend/src/TerminalPane.tsx`: rendering, width policy, and input claims.
- `frontend/src/style.css`: mobile chrome and keyboard layout rules.

## Related contracts

- [Workspace layout](workspace-layout.md): projection and durable geometry.
- [Device presence](device-presence.md): activity and notification routing.
- [Terminal input](terminal-input.md): input ownership and keyboard handling.
