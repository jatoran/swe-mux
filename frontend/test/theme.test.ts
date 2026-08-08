import assert from 'node:assert/strict'
import test from 'node:test'
import { terminalThemes, themeOptions, themePreviewColors } from '../src/theme.ts'

test('theme catalog has one entry for every selectable palette', () => {
  const optionNames=themeOptions.map(option=>option.name)
  assert.equal(new Set(optionNames).size,optionNames.length)
  assert.deepEqual(
    optionNames.filter(name=>name!=='system').sort(),
    Object.keys(terminalThemes).sort(),
  )
})

test('every concrete theme exposes a fixed six-color preview', () => {
  for(const option of themeOptions){
    if(option.name==='system')continue
    const colors=themePreviewColors(option.name)
    assert.equal(colors.length,6,option.name)
    assert.ok(colors.every(color=>/^#[0-9a-f]{6}$/i.test(color)),option.name)
  }
})
