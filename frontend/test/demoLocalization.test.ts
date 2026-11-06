import assert from 'node:assert/strict'
import test from 'node:test'
import { applyLanguagePreference } from '../src/localization/index.ts'
import { SCENARIOS } from '../src/demo/scenarios.ts'
import { localizedKeymapFixture } from '../src/demo/keymapPresentation.ts'
import { cellWidth, composerWindow } from '../src/demo/terminalWidth.ts'
import { composerBlock, composerCaret, consumeInput, renderedRows, type LineState } from '../src/demo/terminalSim.ts'

test('demo locale changes copy while preserving every keybinding and command identity', () => {
  try {
    applyLanguagePreference('en')
    const english=localizedKeymapFixture(), labels=SCENARIOS.map(s=>s.label)
    applyLanguagePreference('zh-Hans')
    const chinese=localizedKeymapFixture()
    assert.deepEqual(chinese.rules,english.rules)
    assert.deepEqual(chinese.commands.map(c=>c.id),english.commands.map(c=>c.id))
    for(const id of Object.keys(english.resolution) as Array<keyof typeof english.resolution>) {
      assert.deepEqual(chinese.resolution[id].bindings,english.resolution[id].bindings)
    }
    assert.match(chinese.commands.find(c=>c.id==='palette.open')!.label,/[\u3400-\u9fff]/)
    assert.match(chinese.presets.find(p=>p.id==='tmux')!.description,/[\u3400-\u9fff]/)
    assert(SCENARIOS.every((s,index)=>s.label!==labels[index]))
    assert.match(SCENARIOS.find(s=>s.id==='palette')!.beats[1].field!.text,/[\u3400-\u9fff]/)
  } finally { applyLanguagePreference('en') }
})
test('Chinese composer uses terminal cells and keeps its caret inside the box', () => {
  assert.equal(cellWidth('你好abc'),7)
  assert.deepEqual(composerWindow('你好世界',4,6),{text:'好世界',caret:6})
  assert.equal(composerCaret('你好'),'\x1b[2A\x1b[9G')
  const rows=composerBlock({kind:'claude',model:'demo',contextPct:0},'你好').split('\r\n')
  assert.equal(cellWidth(rows[0]),40)
  assert.equal(cellWidth(rows[1]),40)
  assert.equal(renderedRows('a你你\r\n',4),2)
})
test('demo editing preserves Chinese and complete graphemes through insertion and deletion', () => {
  const line:LineState={buffer:''}
  consumeInput(line,'你好🙂')
  consumeInput(line,'\x1b[D\x1b[3~')
  assert.equal(line.buffer,'你好')
  consumeInput(line,'\x7f')
  assert.equal(line.buffer,'你')
  consumeInput(line,'e\u0301')
  consumeInput(line,'\x7f')
  assert.equal(line.buffer,'你')
  assert.equal(consumeInput(line,'\r').submitted,'你')
})
