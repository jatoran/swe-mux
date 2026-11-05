import { IntlMessageFormat } from 'intl-messageformat'

/** Argument multiplicity, formatting, and each conditional branch are part of a message's contract. */
export function messageContract(pattern: string): string {
  const result: string[] = []
  const walk = (parts: ReturnType<IntlMessageFormat['getAst']>, path: string) => {
    for (const part of parts) {
      if (part.type === 0) continue
      const options = 'options' in part ? Object.keys(part.options).sort() : []
      result.push(JSON.stringify([
        path, part.type, 'value' in part ? part.value : '',
        'offset' in part ? part.offset : 0,
        'style' in part ? part.style : null, options,
      ]))
      if ('options' in part) {
        for (const key of options) walk(part.options[key].value, path + '/' + part.type + ':' + part.value + ':' + key)
      }
    }
  }
  walk(new IntlMessageFormat(pattern, 'en', undefined, { ignoreTag: true }).getAst(), '')
  return result.sort().join('\n')
}
