const SVG_NS = 'http://www.w3.org/2000/svg'

export type NoteRailIcon = 'copy' | 'paste'

type SvgShape = {
  tag: 'path' | 'rect'
  attributes: Record<string, string>
}

const ICON_SHAPES: Record<NoteRailIcon, readonly SvgShape[]> = {
  copy: [
    { tag: 'rect', attributes: { x: '8', y: '8', width: '14', height: '14', rx: '2' } },
    { tag: 'path', attributes: { d: 'M4 16a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2' } },
  ],
  paste: [
    { tag: 'rect', attributes: { x: '8', y: '2', width: '8', height: '4', rx: '1' } },
    { tag: 'path', attributes: { d: 'M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2' } },
  ],
}

/** Build a fresh SVG element for Continuity's host-action icon slot. */
export function createNoteRailIcon(kind: NoteRailIcon, ownerDocument: Document = document): SVGSVGElement {
  const svg = ownerDocument.createElementNS(SVG_NS, 'svg')
  const attributes = {
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    'stroke-width': '2',
    'stroke-linecap': 'round',
    'stroke-linejoin': 'round',
    'aria-hidden': 'true',
    focusable: 'false',
  }
  for (const [name, value] of Object.entries(attributes)) svg.setAttribute(name, value)
  for (const shape of ICON_SHAPES[kind]) {
    const element = ownerDocument.createElementNS(SVG_NS, shape.tag)
    // Continuity gives SVG icons `fill: currentColor`; setting the geometry itself
    // to no fill keeps these conventional outline marks legible at rail size.
    element.setAttribute('fill', 'none')
    for (const [name, value] of Object.entries(shape.attributes)) element.setAttribute(name, value)
    svg.append(element)
  }
  return svg
}
