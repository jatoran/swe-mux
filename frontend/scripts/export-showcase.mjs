/** Capture the public demo headlessly, then encode reviewed feature assets. No daemon. */
import { createHash } from 'node:crypto'
import { execFileSync, spawn } from 'node:child_process'
import { mkdir, readFile, readdir, rename, stat, writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const output = join(root, 'site/img')
const capture = join(root, 'trailer/demo-capture')
// Still selection is by the scenario's beat number, not by elapsed recording time.
const entries = [
  ['status', 'status', 'desktop', 3],
  ['phone', 'status', 'phone', 3],
  ['voice', 'voice', 'desktop', 5],
  ['input', 'input', 'desktop', 5],
  ['attachment', 'attachment', 'desktop', 3],
  ['panes', 'preview', 'desktop', 7],
  ['communication', 'orchestrate', 'desktop', 7],
  ['land', 'land', 'desktop', 5],
  ['landfailure', 'landfailure', 'desktop', 5],
  ['history', 'history', 'desktop', 2],
  ['clipboard', 'clipboard', 'desktop', 2],
]
const selected = process.argv.includes('--only')
  ? new Set((process.argv[process.argv.indexOf('--only') + 1] || '').split(',')) : null
if (selected && [...selected].some(name => !entries.some(entry => entry[0] === name))) throw new Error('Unknown showcase name')

try {
  if (process.platform === 'win32') execFileSync('powershell.exe', ['-NoProfile', '-Command', `(Get-Process -Id ${process.pid}).PriorityClass = 'BelowNormal'`], { windowsHide: true, stdio: 'ignore' })
} catch { process.stderr.write('showcase: could not lower process priority\n') }

async function run(command, args) {
  await new Promise((done, fail) => {
    const child = spawn(command, args, { cwd: root, windowsHide: true, stdio: 'inherit' })
    child.once('error', fail)
    child.once('exit', code => code === 0 ? done() : fail(new Error(`${command} exited ${code}`)))
  })
}

async function archive(file) {
  try { await stat(file) } catch (error) { if (error.code === 'ENOENT') return; throw error }
  const dir = join(root, '.trash', `showcase-${Date.now()}`)
  await mkdir(dir, { recursive: true })
  await rename(file, join(dir, file.split(/[\\/]/).at(-1)))
}

const hash = createHash('sha256')
for (const name of ['index.html', ...(await readdir(join(root, 'site/demo/assets'))).sort().map(name => `assets/${name}`)]) {
  hash.update(name); hash.update(await readFile(join(root, 'site/demo', name)))
}
const bundle = hash.digest('hex')
let previous = []
try { previous = JSON.parse(await readFile(join(output, 'showcase-manifest.json'), 'utf8')).assets } catch {}
const completed = previous.filter(entry => !entries.some(item => item[0] === entry.name && (!selected || selected.has(item[0]))))
await mkdir(output, { recursive: true })
async function exportEntry([name, scenario, surface, beat]) {
  await run(process.execPath, ['frontend/scripts/capture-demo.mjs', '--scenario', scenario, '--surface', surface])
  const take = join(capture, `${scenario}-${surface}`)
  const manifest = JSON.parse(await readFile(join(take, 'manifest.json'), 'utf8'))
  const source = join(take, manifest.video)
  const stage = join(take, 'encoded')
  await mkdir(stage, { recursive: true })
  const stem = join(stage, `showcase-${name}`)
  const scale = surface === 'phone' ? '390:-2' : '1440:-2'
  const common = ['-hide_banner','-loglevel','error','-i',source,'-vf',`scale=${scale}:flags=lanczos,fps=24`,'-an','-threads','2']
  await run('ffmpeg', [...common, '-c:v','libx264','-crf','23','-preset','medium','-pix_fmt','yuv420p','-movflags','+faststart',`${stem}.mp4`])
  await run('ffmpeg', [...common, '-c:v','libvpx-vp9','-crf','34','-b:v','0','-row-mt','1',`${stem}.webm`])
  const still = manifest.stills.find(item => item.beat === beat)
  if (!still) throw new Error(`${scenario}: missing still for beat ${beat}`)
  await run('ffmpeg', ['-hide_banner','-loglevel','error','-i',join(take,still.file),'-vf',`scale=${scale}:flags=lanczos`,'-c:v','libwebp','-quality','90',`${stem}.webp`])
  for (const ext of ['webm','mp4','webp']) {
    const destination = join(output, `showcase-${name}.${ext}`)
    await archive(destination)
    await rename(`${stem}.${ext}`, destination)
  }
  completed.push({ name, scenario, surface, beat, caption: still.say, bundle, seed: manifest.seed, simulated: true })
  process.stdout.write(`showcase: exported ${name}\n`)
}

const pending = entries.filter(entry => !selected || selected.has(entry[0]))
// Two isolated browsers, with bounded encoder threads and inherited low priority.
const outcomes = await Promise.allSettled(Array.from({ length: 2 }, async () => {
  while (pending.length) {
    const entry = pending.shift()
    if (entry) await exportEntry(entry)
  }
}))
completed.sort((a,b) => a.name.localeCompare(b.name))
await writeFile(join(output, 'showcase-manifest.json'), JSON.stringify({ version: 1, assets: completed }, null, 2) + '\n')

const errors = outcomes.filter(result => result.status === 'rejected')
if (errors.length) throw new AggregateError(errors.map(result => result.reason), 'Showcase capture failed')
