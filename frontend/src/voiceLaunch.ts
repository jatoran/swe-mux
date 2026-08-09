/**
 * Spoken aliases for one ordinary session-spawn command.
 *
 * Names, stable visible Project numbers, and the selected-Project shorthand all
 * compile into the existing command registry. The action stays in App's normal
 * spawn path, so voice does not acquire a parallel launcher.
 */
export function sessionLaunchVoicePhrases({
  backend,
  displayName,
  projectName,
  projectNumber,
  currentProject,
}: {
  backend: string
  displayName: string
  projectName: string
  projectNumber: number | null
  currentProject: boolean
}): string[] {
  const names = new Set([displayName, backend])
  if (backend === 'shell') names.add('terminal')

  const targets = [`in ${projectName}`]
  if (projectNumber !== null) targets.push(`in project ${projectNumber}`)
  if (currentProject) targets.push('')

  const phrases: string[] = []
  for (const name of names) {
    for (const target of targets) {
      const phrase = `new ${name}${target ? ` ${target}` : ''}`
      phrases.push(phrase, `${phrase} {text}`)
    }
  }
  return [...new Set(phrases)]
}
