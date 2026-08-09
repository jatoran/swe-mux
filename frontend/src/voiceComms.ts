export const VOICE_COMMS_PROTOCOL = [
  'Voice comms mode is active.',
  'For messages prefixed [voice], answer in one or two natural spoken sentences.',
  'Lead with the answer.',
  'Avoid markdown, lists, code, and file paths unless explicitly requested.',
  'Ask at most one short question when clarification is required.',
  'Messages without [voice] use normal detail.',
].join(' ')

export function voiceCommsMessage(text: string, includeProtocol: boolean): string {
  const message = `[voice] ${text.trim()}`
  return includeProtocol ? `${VOICE_COMMS_PROTOCOL}\n\n${message}` : message
}
