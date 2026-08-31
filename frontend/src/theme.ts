export type ThemeName = 'light' | 'dark' | 'system' | 'solarized-dark' | 'tokyo-night' | 'gruvbox-dark' | 'catppuccin-mocha' | 'catppuccin-latte' | 'nord' | 'dracula' | 'everforest-dark' | 'rose-pine' | 'kanagawa' | 'ayu-dark' | 'tron' | 'synthwave-84' | 'cyberpunk-neon' | 'amber-crt' | 'green-phosphor' | 'borland-dos' | 'phosphor-blue' | 'phosphor-purple' | 'commodore-64' | 'amiga-workbench' | 'cga' | 'macintosh-system-6' | 'game-boy' | 'virtual-boy' | 'custom'

export const themeOptions: ReadonlyArray<{name:ThemeName;label:string}> = [
  {name:'dark',label:'Dark'},
  {name:'light',label:'Light'},
  {name:'system',label:'System'},
  {name:'solarized-dark',label:'Solarized Dark'},
  {name:'tokyo-night',label:'Tokyo Night'},
  {name:'gruvbox-dark',label:'Gruvbox Dark'},
  {name:'catppuccin-mocha',label:'Catppuccin Mocha'},
  {name:'catppuccin-latte',label:'Catppuccin Latte'},
  {name:'nord',label:'Nord'},
  {name:'dracula',label:'Dracula'},
  {name:'everforest-dark',label:'Everforest Dark'},
  {name:'rose-pine',label:'Rosé Pine'},
  {name:'kanagawa',label:'Kanagawa'},
  {name:'ayu-dark',label:'Ayu Dark'},
  {name:'tron',label:'Tron'},
  {name:'synthwave-84',label:"Synthwave '84"},
  {name:'cyberpunk-neon',label:'Cyberpunk Neon'},
  {name:'amber-crt',label:'Amber CRT'},
  {name:'green-phosphor',label:'Green Phosphor'},
  {name:'borland-dos',label:'Borland DOS'},
  {name:'phosphor-blue',label:'Phosphor Blue'},
  {name:'phosphor-purple',label:'Phosphor Purple'},
  {name:'commodore-64',label:'Commodore 64'},
  {name:'amiga-workbench',label:'Amiga Workbench'},
  {name:'cga',label:'CGA'},
  {name:'macintosh-system-6',label:'Macintosh System 6'},
  {name:'game-boy',label:'Game Boy'},
  {name:'virtual-boy',label:'Virtual Boy'},
  {name:'custom',label:'Custom'},
]

export type ResolvedThemeName = Exclude<ThemeName, 'system'>
export type BrowserColorScheme = 'light' | 'dark'

export const terminalThemes: Record<ResolvedThemeName, Record<string, string>> = {
  dark: { background:'#090a0c', foreground:'#d9dde2', cursor:'#d9dde2', selectionBackground:'#3b4658', black:'#15171b', brightBlack:'#686f7a', red:'#f07178', brightRed:'#ff8b92', green:'#8bd450', brightGreen:'#b1f477', yellow:'#e7c768', brightYellow:'#f5dc89', blue:'#72a7ff', brightBlue:'#9bc0ff', magenta:'#c792ea', brightMagenta:'#ddb0f5', cyan:'#6fd3d8', brightCyan:'#91e5e9', white:'#d9dde2', brightWhite:'#ffffff' },
  light: { background:'#f5f2e9', foreground:'#252821', cursor:'#252821', selectionBackground:'#b8c9dc', black:'#252821', brightBlack:'#6e7068', red:'#b7323c', brightRed:'#d4434e', green:'#3f7625', brightGreen:'#558d34', yellow:'#8c6810', brightYellow:'#a77e19', blue:'#315b9c', brightBlue:'#4772b5', magenta:'#794b8e', brightMagenta:'#9561aa', cyan:'#27727a', brightCyan:'#388b94', white:'#dedbd1', brightWhite:'#ffffff' },
  'solarized-dark': { background:'#002b36', foreground:'#93a1a1', cursor:'#b58900', selectionBackground:'#174957', black:'#073642', brightBlack:'#586e75', red:'#dc322f', brightRed:'#cb4b16', green:'#859900', brightGreen:'#586e75', yellow:'#b58900', brightYellow:'#657b83', blue:'#268bd2', brightBlue:'#839496', magenta:'#d33682', brightMagenta:'#6c71c4', cyan:'#2aa198', brightCyan:'#93a1a1', white:'#eee8d5', brightWhite:'#fdf6e3' },
  'tokyo-night': { background:'#1a1b26', foreground:'#c0caf5', cursor:'#c0caf5', selectionBackground:'#33467c', black:'#15161e', brightBlack:'#565f89', red:'#f7768e', brightRed:'#ff899d', green:'#9ece6a', brightGreen:'#b9e986', yellow:'#e0af68', brightYellow:'#f2c47e', blue:'#7aa2f7', brightBlue:'#91b4ff', magenta:'#bb9af7', brightMagenta:'#cdb0ff', cyan:'#7dcfff', brightCyan:'#a4ddff', white:'#a9b1d6', brightWhite:'#c0caf5' },
  'gruvbox-dark': { background:'#282828', foreground:'#ebdbb2', cursor:'#ebdbb2', selectionBackground:'#504945', black:'#282828', brightBlack:'#928374', red:'#cc241d', brightRed:'#fb4934', green:'#98971a', brightGreen:'#b8bb26', yellow:'#d79921', brightYellow:'#fabd2f', blue:'#458588', brightBlue:'#83a598', magenta:'#b16286', brightMagenta:'#d3869b', cyan:'#689d6a', brightCyan:'#8ec07c', white:'#a89984', brightWhite:'#ebdbb2' },
  'catppuccin-mocha': { background:'#1e1e2e', foreground:'#cdd6f4', cursor:'#f5e0dc', selectionBackground:'#585b70', black:'#45475a', brightBlack:'#585b70', red:'#f38ba8', brightRed:'#f38ba8', green:'#a6e3a1', brightGreen:'#a6e3a1', yellow:'#f9e2af', brightYellow:'#f9e2af', blue:'#89b4fa', brightBlue:'#89b4fa', magenta:'#f5c2e7', brightMagenta:'#f5c2e7', cyan:'#94e2d5', brightCyan:'#94e2d5', white:'#bac2de', brightWhite:'#a6adc8' },
  'catppuccin-latte': { background:'#eff1f5', foreground:'#4c4f69', cursor:'#dc8a78', selectionBackground:'#bcc0cc', black:'#5c5f77', brightBlack:'#6c6f85', red:'#d20f39', brightRed:'#d20f39', green:'#40a02b', brightGreen:'#40a02b', yellow:'#df8e1d', brightYellow:'#df8e1d', blue:'#1e66f5', brightBlue:'#1e66f5', magenta:'#ea76cb', brightMagenta:'#ea76cb', cyan:'#179299', brightCyan:'#179299', white:'#acb0be', brightWhite:'#bcc0cc' },
  nord: { background:'#2e3440', foreground:'#d8dee9', cursor:'#d8dee9', selectionBackground:'#434c5e', black:'#3b4252', brightBlack:'#4c566a', red:'#bf616a', brightRed:'#bf616a', green:'#a3be8c', brightGreen:'#a3be8c', yellow:'#ebcb8b', brightYellow:'#ebcb8b', blue:'#81a1c1', brightBlue:'#81a1c1', magenta:'#b48ead', brightMagenta:'#b48ead', cyan:'#88c0d0', brightCyan:'#8fbcbb', white:'#e5e9f0', brightWhite:'#eceff4' },
  dracula: { background:'#282a36', foreground:'#f8f8f2', cursor:'#f8f8f2', selectionBackground:'#44475a', black:'#21222c', brightBlack:'#6272a4', red:'#ff5555', brightRed:'#ff6e6e', green:'#50fa7b', brightGreen:'#69ff94', yellow:'#f1fa8c', brightYellow:'#ffffa5', blue:'#bd93f9', brightBlue:'#d6acff', magenta:'#ff79c6', brightMagenta:'#ff92df', cyan:'#8be9fd', brightCyan:'#a4ffff', white:'#f8f8f2', brightWhite:'#ffffff' },
  'everforest-dark': { background:'#2d353b', foreground:'#d3c6aa', cursor:'#d3c6aa', selectionBackground:'#475258', black:'#475258', brightBlack:'#859289', red:'#e67e80', brightRed:'#e67e80', green:'#a7c080', brightGreen:'#a7c080', yellow:'#dbbc7f', brightYellow:'#dbbc7f', blue:'#7fbbb3', brightBlue:'#7fbbb3', magenta:'#d699b6', brightMagenta:'#d699b6', cyan:'#83c092', brightCyan:'#83c092', white:'#d3c6aa', brightWhite:'#d3c6aa' },
  'rose-pine': { background:'#191724', foreground:'#e0def4', cursor:'#e0def4', selectionBackground:'#403d52', black:'#26233a', brightBlack:'#6e6a86', red:'#eb6f92', brightRed:'#eb6f92', green:'#31748f', brightGreen:'#31748f', yellow:'#f6c177', brightYellow:'#f6c177', blue:'#9ccfd8', brightBlue:'#9ccfd8', magenta:'#c4a7e7', brightMagenta:'#c4a7e7', cyan:'#ebbcba', brightCyan:'#ebbcba', white:'#e0def4', brightWhite:'#e0def4' },
  kanagawa: { background:'#1f1f28', foreground:'#dcd7ba', cursor:'#c8c093', selectionBackground:'#2d4f67', black:'#16161d', brightBlack:'#727169', red:'#c34043', brightRed:'#e82424', green:'#76946a', brightGreen:'#98bb6c', yellow:'#c0a36e', brightYellow:'#e6c384', blue:'#7e9cd8', brightBlue:'#7fb4ca', magenta:'#957fb8', brightMagenta:'#938aa9', cyan:'#6a9589', brightCyan:'#7aa89f', white:'#c8c093', brightWhite:'#dcd7ba' },
  'ayu-dark': { background:'#0b0e14', foreground:'#bfbdb6', cursor:'#e6b450', selectionBackground:'#273747', black:'#131721', brightBlack:'#565b66', red:'#ea6c73', brightRed:'#f07178', green:'#7fd962', brightGreen:'#aad94c', yellow:'#f9af4f', brightYellow:'#ffb454', blue:'#53bdfa', brightBlue:'#59c2ff', magenta:'#cda1fa', brightMagenta:'#d2a6ff', cyan:'#90e1c6', brightCyan:'#95e6cb', white:'#bfbdb6', brightWhite:'#ffffff' },
  tron: { background:'#061014', foreground:'#b8e6ff', cursor:'#ff9d2e', selectionBackground:'#12384a', black:'#0a1a22', brightBlack:'#4d7f96', red:'#ff4d3d', brightRed:'#ff7a5c', green:'#22d3a0', brightGreen:'#5cf2c4', yellow:'#ff9d2e', brightYellow:'#ffbe5c', blue:'#2ea8ff', brightBlue:'#6ec8ff', magenta:'#b07bff', brightMagenta:'#c9a3ff', cyan:'#37e6ff', brightCyan:'#86f2ff', white:'#b8e6ff', brightWhite:'#eafaff' },
  'synthwave-84': { background:'#262335', foreground:'#f4f4f8', cursor:'#ff7edb', selectionBackground:'#463465', black:'#241b2f', brightBlack:'#7c6f9b', red:'#fe4450', brightRed:'#ff6e78', green:'#72f1b8', brightGreen:'#9effd4', yellow:'#fede5d', brightYellow:'#fff29a', blue:'#8a7cff', brightBlue:'#a99cff', magenta:'#ff7edb', brightMagenta:'#ffa3e7', cyan:'#36f9f6', brightCyan:'#7dfffc', white:'#f4f4f8', brightWhite:'#ffffff' },
  'cyberpunk-neon': { background:'#000b1e', foreground:'#0abdc6', cursor:'#ea00d9', selectionBackground:'#123e7c', black:'#071932', brightBlack:'#2e5f8c', red:'#ff2e6b', brightRed:'#ff5f8f', green:'#00ff9f', brightGreen:'#5cffc0', yellow:'#f57800', brightYellow:'#ffa23e', blue:'#1f6fff', brightBlue:'#4f95ff', magenta:'#ea00d9', brightMagenta:'#ff5ceb', cyan:'#0abdc6', brightCyan:'#4de3ea', white:'#d7d7d5', brightWhite:'#ffffff' },
  'amber-crt': { background:'#140d00', foreground:'#ffb000', cursor:'#ffcc55', selectionBackground:'#4a3200', black:'#241800', brightBlack:'#8a6000', red:'#ff5a1f', brightRed:'#ff8c4d', green:'#ffb000', brightGreen:'#ffc94d', yellow:'#ffc300', brightYellow:'#ffd966', blue:'#d99000', brightBlue:'#ffb833', magenta:'#ff8f1f', brightMagenta:'#ffab52', cyan:'#ffa000', brightCyan:'#ffc270', white:'#ffb000', brightWhite:'#ffe0a3' },
  'green-phosphor': { background:'#001100', foreground:'#33ff33', cursor:'#33ff33', selectionBackground:'#0a3d0a', black:'#001a00', brightBlack:'#158f15', red:'#00ff88', brightRed:'#5cffb4', green:'#22cc22', brightGreen:'#4dff4d', yellow:'#a6ff4d', brightYellow:'#c8ff85', blue:'#00e0a0', brightBlue:'#2effc0', magenta:'#7cff29', brightMagenta:'#9cff5c', cyan:'#00e0c0', brightCyan:'#4dffe0', white:'#33ff33', brightWhite:'#ccffcc' },
  'borland-dos': { background:'#0000a8', foreground:'#ffff54', cursor:'#ffffff', selectionBackground:'#1414b4', black:'#000000', brightBlack:'#545454', red:'#fe5454', brightRed:'#ff8a8a', green:'#54fe54', brightGreen:'#8aff8a', yellow:'#ffff54', brightYellow:'#ffff9c', blue:'#5454fe', brightBlue:'#8a8aff', magenta:'#fe54fe', brightMagenta:'#ff8aff', cyan:'#54fefe', brightCyan:'#8affff', white:'#e6e6ff', brightWhite:'#ffffff' },
  'phosphor-blue': { background:'#020817', foreground:'#9dd7ff', cursor:'#c6eaff', selectionBackground:'#173d69', black:'#050d1a', brightBlack:'#326a97', red:'#62b9ff', brightRed:'#93d4ff', green:'#32a6ff', brightGreen:'#64c7ff', yellow:'#9ad8ff', brightYellow:'#c2e9ff', blue:'#247ad4', brightBlue:'#4ba2ff', magenta:'#788cff', brightMagenta:'#a7b7ff', cyan:'#24c7e8', brightCyan:'#70eaff', white:'#9dd7ff', brightWhite:'#e5f7ff' },
  'phosphor-purple': { background:'#0b0312', foreground:'#dda6ff', cursor:'#efceff', selectionBackground:'#4a2065', black:'#11051c', brightBlack:'#70448a', red:'#d56aff', brightRed:'#efa0ff', green:'#ae58e8', brightGreen:'#d083ff', yellow:'#e5b1ff', brightYellow:'#f5d8ff', blue:'#8456d8', brightBlue:'#aa83f3', magenta:'#c45cff', brightMagenta:'#e692ff', cyan:'#a57ae8', brightCyan:'#ccb0f5', white:'#dda6ff', brightWhite:'#f8ebff' },
  'commodore-64': { background:'#40318d', foreground:'#bbb7f2', cursor:'#ffffff', selectionBackground:'#6759b2', black:'#000000', brightBlack:'#4a4a4a', red:'#813338', brightRed:'#c46c71', green:'#56ac4d', brightGreen:'#a9ff9f', yellow:'#d8d46d', brightYellow:'#edf171', blue:'#2e2c9b', brightBlue:'#706deb', magenta:'#8e3c97', brightMagenta:'#c96fd1', cyan:'#75cec8', brightCyan:'#9ff3ed', white:'#b2b2b2', brightWhite:'#ffffff' },
  'amiga-workbench': { background:'#0050a4', foreground:'#ffffff', cursor:'#ff8800', selectionBackground:'#1671c1', black:'#000000', brightBlack:'#4f6680', red:'#dd3322', brightRed:'#ff6655', green:'#00aa88', brightGreen:'#44ddbb', yellow:'#ff8800', brightYellow:'#ffbb55', blue:'#0055aa', brightBlue:'#55aaff', magenta:'#aa55aa', brightMagenta:'#dd88dd', cyan:'#55cccc', brightCyan:'#99eeee', white:'#cccccc', brightWhite:'#ffffff' },
  cga: { background:'#000000', foreground:'#aaaaaa', cursor:'#ffffff', selectionBackground:'#555555', black:'#000000', brightBlack:'#555555', red:'#aa0000', brightRed:'#ff5555', green:'#00aa00', brightGreen:'#55ff55', yellow:'#aa5500', brightYellow:'#ffff55', blue:'#0000aa', brightBlue:'#5555ff', magenta:'#aa00aa', brightMagenta:'#ff55ff', cyan:'#00aaaa', brightCyan:'#55ffff', white:'#aaaaaa', brightWhite:'#ffffff' },
  'macintosh-system-6': { background:'#f5f5ed', foreground:'#111111', cursor:'#111111', selectionBackground:'#a8c4df', black:'#111111', brightBlack:'#676762', red:'#a32323', brightRed:'#cc4545', green:'#2d6a4f', brightGreen:'#438c6b', yellow:'#8a6410', brightYellow:'#b08524', blue:'#285f9e', brightBlue:'#3f7ec2', magenta:'#72527f', brightMagenta:'#916b9f', cyan:'#34747a', brightCyan:'#4d959c', white:'#deded6', brightWhite:'#ffffff' },
  'game-boy': { background:'#0f380f', foreground:'#b6d44a', cursor:'#d5ef68', selectionBackground:'#306230', black:'#0f380f', brightBlack:'#306230', red:'#6c4a24', brightRed:'#a56b32', green:'#5f7f18', brightGreen:'#9bbc0f', yellow:'#8bac0f', brightYellow:'#d5ef68', blue:'#365f38', brightBlue:'#609260', magenta:'#556b2f', brightMagenta:'#83a343', cyan:'#4f7a3a', brightCyan:'#78a85a', white:'#8bac0f', brightWhite:'#d5ef68' },
  'virtual-boy': { background:'#000000', foreground:'#ff3045', cursor:'#ff7584', selectionBackground:'#5e000b', black:'#000000', brightBlack:'#5e000b', red:'#a80018', brightRed:'#ff1830', green:'#8f1424', brightGreen:'#d52a3e', yellow:'#c12738', brightYellow:'#ff596b', blue:'#70101d', brightBlue:'#bb3442', magenta:'#a7182b', brightMagenta:'#eb4254', cyan:'#8b2732', brightCyan:'#d35f6e', white:'#cf2639', brightWhite:'#ff9da7' },
  custom: { background:'#090a0c', foreground:'#d9dde2', cursor:'#8bd450', selectionBackground:'#3b4658', black:'#15171b', brightBlack:'#686f7a', red:'#f07178', brightRed:'#f07178', green:'#8bd450', brightGreen:'#8bd450', yellow:'#e7c768', brightYellow:'#e7c768', blue:'#72a7ff', brightBlue:'#72a7ff', magenta:'#c792ea', brightMagenta:'#c792ea', cyan:'#6fd3d8', brightCyan:'#6fd3d8', white:'#d9dde2', brightWhite:'#ffffff' },
}

export function themePreviewColors(name: ThemeName,customTheme?:CustomTheme): string[] {
  if(name==='custom'&&customTheme)return [customTheme.background,customTheme.foreground,customTheme.error,customTheme.accent,customTheme.line,customTheme.muted]
  const palette = terminalThemes[resolvedTheme(name)]
  return [palette.background,palette.foreground,palette.red,palette.green,palette.blue,palette.magenta]
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

export function resolvedTheme(name: ThemeName): ResolvedThemeName {
  if (name !== 'system') return name
  return matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

function linearColorChannel(value: number): number {
  const channel=value/255
  return channel<=0.04045?channel/12.92:((channel+0.055)/1.055)**2.4
}

/** Pick native control and browser chrome treatment from the theme's actual canvas. */
export function browserColorScheme(background: string): BrowserColorScheme {
  const red=linearColorChannel(Number.parseInt(background.slice(1,3),16))
  const green=linearColorChannel(Number.parseInt(background.slice(3,5),16))
  const blue=linearColorChannel(Number.parseInt(background.slice(5,7),16))
  // At this luminance black and white have equal WCAG contrast. The side with
  // the stronger contrast is the honest light/dark description for native UI.
  return 0.2126*red+0.7152*green+0.0722*blue>0.179?'light':'dark'
}

export function themeDocumentPresentation(name: ThemeName) {
  const resolved=resolvedTheme(name)
  const background=terminalThemes[resolved].background
  return {resolved,background,scheme:browserColorScheme(background)} as const
}

export function applyTheme(name: ThemeName) {
  if (name !== 'custom') {
    for (const token of ['--bg','--panel','--panel2','--line','--text','--muted','--green','--green2','--red']) document.documentElement.style.removeProperty(token)
  }
  const presentation=themeDocumentPresentation(name)
  document.documentElement.dataset.theme = presentation.resolved
  document.documentElement.dataset.themeSelection = name
  document.documentElement.style.colorScheme=`only ${presentation.scheme}`
  document.querySelector<HTMLMetaElement>('meta[name="color-scheme"]')?.setAttribute('content',`only ${presentation.scheme}`)
  document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')?.setAttribute('content',presentation.background)
  window.dispatchEvent(new CustomEvent('mux:theme', { detail: presentation.resolved }))
}
