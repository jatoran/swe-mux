type ProjectDefault = {
  id:string
  default_backend?:string|null
  default_profile_id?:string|null
}

function canonicalize(value:unknown):unknown {
  if (Array.isArray(value)) return value.map(canonicalize)
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string,unknown>)
        .sort(([left],[right])=>left.localeCompare(right))
        .map(([key,item])=>[key,canonicalize(item)]),
    )
  }
  return value
}

export function sameDraftValue(left:unknown,right:unknown):boolean {
  return JSON.stringify(canonicalize(left))===JSON.stringify(canonicalize(right))
}

export function comparableProjectDefaults(projects:ProjectDefault[]):ProjectDefault[] {
  return projects.map(project=>({
    id:project.id,
    default_backend:project.default_backend||null,
    default_profile_id:project.default_profile_id||null,
  }))
}

export function parseIgnorePatternDraft(text:string):string[] {
  return text.split('\n')
}

export function normalizeIgnorePatterns(patterns:string[]):string[] {
  return patterns.map(pattern=>pattern.trim()).filter(Boolean)
}
