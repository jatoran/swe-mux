export type ProjectResourceNodeKind = 'file' | 'directory'

export function projectResourceCreationParent(path: string, kind: ProjectResourceNodeKind): string {
  if (kind === 'directory') return path
  const separator = path.lastIndexOf('/')
  return separator < 0 ? '' : path.slice(0, separator)
}
