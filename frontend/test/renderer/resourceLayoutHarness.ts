import '../../src/style.css'

const root = document.querySelector<HTMLElement>('#root')!
root.innerHTML = `
  <section class="project-resource file-editor">
    <header>
      <div class="autosave-resource-heading">
        <strong>spaces/JAR Systems/projects/career-role-proposal/Director_of_Digital_Products_and_Data.md</strong>
        <span class="resource-save-indicator saved"></span>
      </div>
      <div class="resource-actions">
        <button>→ agent</button>
        <button>⌕</button>
        <button>☰</button>
      </div>
    </header>
    <continuity-editor class="note-editor" style="display:block;flex:1;width:100%;height:100%;min-width:0;min-height:0"></continuity-editor>
  </section>
`
root.style.cssText = 'width:100%;height:100dvh;display:grid;grid-template-rows:minmax(0,1fr);margin:0'
document.body.style.margin = '0'
document.documentElement.style.setProperty('--ui-scale', '1')
