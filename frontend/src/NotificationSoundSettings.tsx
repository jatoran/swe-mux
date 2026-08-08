// Compatibility export for extensions that imported the former split settings panel.
// Settings renders NotificationAlertSettings once; sound and push now share one surface.
export { NotificationAlertSettings as NotificationSoundSettings } from './NotificationPushSettings.tsx'
