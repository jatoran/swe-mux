import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { parseDelimitedText } from './delimitedText'

const ROW_HEIGHT = 29
const COLUMN_WIDTH = 180
const ROW_NUMBER_WIDTH = 52
const OVERSCAN_ROWS = 12

const displayCell = (value: string) => value.replace(/\r?\n/g, ' ↵ ')

export function DelimitedTextViewer({ text, delimiter }: { text: string; delimiter: ',' | '\t' }) {
  const preview = useMemo(() => parseDelimitedText(text, delimiter), [text, delimiter])
  const [firstRowHeader, setFirstRowHeader] = useState(true)
  const [scrollTop, setScrollTop] = useState(0)
  const [viewportHeight, setViewportHeight] = useState(400)
  const scroller = useRef<HTMLDivElement>(null)
  const hasHeader = firstRowHeader && preview.rows.length > 0
  const bodyRows = hasHeader ? preview.rows.slice(1) : preview.rows
  const headerHeight = hasHeader ? ROW_HEIGHT : 0
  const start = Math.max(0, Math.floor((scrollTop - headerHeight) / ROW_HEIGHT) - OVERSCAN_ROWS)
  const visibleCount = Math.ceil(viewportHeight / ROW_HEIGHT) + OVERSCAN_ROWS * 2
  const end = Math.min(bodyRows.length, start + visibleCount)
  const columnCount = Math.max(preview.columnCount, 1)
  const tableWidth = ROW_NUMBER_WIDTH + columnCount * COLUMN_WIDTH
  const gridTemplateColumns = `${ROW_NUMBER_WIDTH}px repeat(${columnCount}, ${COLUMN_WIDTH}px)`

  useEffect(() => {
    const element = scroller.current
    if (!element) return
    const update = () => setViewportHeight(element.clientHeight || 400)
    update()
    const observer = new ResizeObserver(update)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  return <div class="delimited-viewer">
    <div class="delimited-toolbar">
      <span>{delimiter === '\t' ? 'TSV' : 'CSV'} · {preview.rows.length.toLocaleString()} preview rows · {preview.columnCount.toLocaleString()} columns</span>
      <label><input type="checkbox" checked={firstRowHeader} onChange={event => setFirstRowHeader(event.currentTarget.checked)} /> First row is header</label>
    </div>
    {(preview.truncated || preview.malformed) && <p class="delimited-warning" role="status">
      {preview.malformed ? 'Malformed quoting detected. ' : ''}
      {preview.truncated ? 'The table preview is bounded; Raw text still contains the complete file.' : ''}
    </p>}
    {!preview.rows.length
      ? <div class="resource-unavailable">This delimited file is empty.</div>
      : <div
          class="delimited-scroll"
          ref={scroller}
          role="table"
          aria-rowcount={preview.rows.length}
          aria-colcount={preview.columnCount}
          onScroll={event => setScrollTop(event.currentTarget.scrollTop)}
        >
          <div class="delimited-canvas" style={{ width: `${tableWidth}px`, height: `${headerHeight + bodyRows.length * ROW_HEIGHT}px` }}>
            {hasHeader && <div class="delimited-row header" role="row" style={{ gridTemplateColumns }}>
              <span class="delimited-row-number" role="columnheader">#</span>
              {Array.from({ length: columnCount }, (_, index) => <strong key={index} role="columnheader" title={preview.rows[0][index] || ''}>{displayCell(preview.rows[0][index] || '')}</strong>)}
            </div>}
            {bodyRows.slice(start, end).map((row, offset) => {
              const bodyIndex = start + offset
              const sourceIndex = bodyIndex + (hasHeader ? 1 : 0)
              return <div
                class="delimited-row body"
                role="row"
                key={sourceIndex}
                style={{ top: `${headerHeight + bodyIndex * ROW_HEIGHT}px`, gridTemplateColumns }}
              >
                <span class="delimited-row-number" role="rowheader">{sourceIndex + 1}</span>
                {Array.from({ length: columnCount }, (_, index) => {
                  const value = row[index] || ''
                  return <span key={index} role="cell" title={value}>{displayCell(value)}</span>
                })}
              </div>
            })}
          </div>
        </div>}
  </div>
}
