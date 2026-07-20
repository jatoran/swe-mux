export function folderNameFromPath(path:string):string {
  const value=path.trim()
  if(!value)return ''
  const withoutTrailing=value.replace(/[\\/]+$/,'')
  if(!withoutTrailing)return '/'
  const name=withoutTrailing.split(/[\\/]/).filter(Boolean).at(-1)||withoutTrailing
  return name.replace(/:$/,'')
}
