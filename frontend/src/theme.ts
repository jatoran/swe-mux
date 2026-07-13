export type ThemeName = 'light' | 'dark' | 'system' | 'solarized-dark' | 'tokyo-night' | 'custom'

export const terminalThemes: Record<Exclude<ThemeName, 'system'>, Record<string, string>> = {
  dark: { background:'#090a0c', foreground:'#d9dde2', cursor:'#d9dde2', selectionBackground:'#3b4658', black:'#15171b', brightBlack:'#686f7a', red:'#f07178', brightRed:'#ff8b92', green:'#8bd450', brightGreen:'#b1f477', yellow:'#e7c768', brightYellow:'#f5dc89', blue:'#72a7ff', brightBlue:'#9bc0ff', magenta:'#c792ea', brightMagenta:'#ddb0f5', cyan:'#6fd3d8', brightCyan:'#91e5e9', white:'#d9dde2', brightWhite:'#ffffff' },
  light: { background:'#f5f2e9', foreground:'#252821', cursor:'#252821', selectionBackground:'#b8c9dc', black:'#252821', brightBlack:'#6e7068', red:'#b7323c', brightRed:'#d4434e', green:'#3f7625', brightGreen:'#558d34', yellow:'#8c6810', brightYellow:'#a77e19', blue:'#315b9c', brightBlue:'#4772b5', magenta:'#794b8e', brightMagenta:'#9561aa', cyan:'#27727a', brightCyan:'#388b94', white:'#dedbd1', brightWhite:'#ffffff' },
  'solarized-dark': { background:'#002b36', foreground:'#93a1a1', cursor:'#b58900', selectionBackground:'#174957', black:'#073642', brightBlack:'#586e75', red:'#dc322f', brightRed:'#cb4b16', green:'#859900', brightGreen:'#586e75', yellow:'#b58900', brightYellow:'#657b83', blue:'#268bd2', brightBlue:'#839496', magenta:'#d33682', brightMagenta:'#6c71c4', cyan:'#2aa198', brightCyan:'#93a1a1', white:'#eee8d5', brightWhite:'#fdf6e3' },
  'tokyo-night': { background:'#1a1b26', foreground:'#c0caf5', cursor:'#c0caf5', selectionBackground:'#33467c', black:'#15161e', brightBlack:'#565f89', red:'#f7768e', brightRed:'#ff899d', green:'#9ece6a', brightGreen:'#b9e986', yellow:'#e0af68', brightYellow:'#f2c47e', blue:'#7aa2f7', brightBlue:'#91b4ff', magenta:'#bb9af7', brightMagenta:'#cdb0ff', cyan:'#7dcfff', brightCyan:'#a4ddff', white:'#a9b1d6', brightWhite:'#c0caf5' },
  custom: { background:'#090a0c', foreground:'#d9dde2', cursor:'#8bd450', selectionBackground:'#3b4658', black:'#15171b', brightBlack:'#686f7a', red:'#f07178', brightRed:'#f07178', green:'#8bd450', brightGreen:'#8bd450', yellow:'#e7c768', brightYellow:'#e7c768', blue:'#72a7ff', brightBlue:'#72a7ff', magenta:'#c792ea', brightMagenta:'#c792ea', cyan:'#6fd3d8', brightCyan:'#6fd3d8', white:'#d9dde2', brightWhite:'#ffffff' },
}

export type CustomTheme = {background:string;panel:string;line:string;foreground:string;muted:string;accent:string;error:string}
export function configureCustomTheme(theme: CustomTheme) {
  const root = document.documentElement.style
  root.setProperty('--bg',theme.background); root.setProperty('--panel',theme.panel)
  root.setProperty('--panel2',theme.panel); root.setProperty('--line',theme.line)
  root.setProperty('--text',theme.foreground); root.setProperty('--muted',theme.muted)
  root.setProperty('--green',theme.accent); root.setProperty('--green2',theme.accent)
  root.setProperty('--red',theme.error)
  terminalThemes.custom = {...terminalThemes.custom,background:theme.background,foreground:theme.foreground,cursor:theme.accent,red:theme.error,brightRed:theme.error,green:theme.accent,brightGreen:theme.accent,white:theme.foreground}
}

export function resolvedTheme(name: ThemeName) {
  if (name !== 'system') return name
  return matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

export function applyTheme(name: ThemeName) {
  if (name !== 'custom') {
    for (const token of ['--bg','--panel','--panel2','--line','--text','--muted','--green','--green2','--red']) document.documentElement.style.removeProperty(token)
  }
  document.documentElement.dataset.theme = resolvedTheme(name)
  document.documentElement.dataset.themeSelection = name
  window.dispatchEvent(new CustomEvent('mux:theme', { detail: resolvedTheme(name) }))
}
