// The sidebar's Project tree, at sidebar width, in the real markup.
//
// It exists for one question a unit test cannot ask: does a Group *look* like a container?
// The tree is a flat run of sections — a Group is a `.sidebar-project-bucket`, an ungrouped
// run is a `.sidebar-ungrouped-projects` — and until this pass the only thing separating
// them was an 8px margin, so a Group holding two Projects with ungrouped Projects beneath
// it read as one list of four. Containment is pure CSS, and pure CSS is exactly what no
// assertion below the browser can see.
//
// Static markup rather than the real component tree: the structure under test is the
// section chrome, and mounting App would drag in the daemon, the layout store, and every
// poller for nothing.
import { render } from 'preact'
import '../../src/style.css'

const projectRow = (name: string, active = false) => <section class={`project-group ${active ? 'active' : ''}`}>
  <div class="project-row draggable-project">
    <span class="project-chevron">▾</span>
    <strong>{name}</strong>
    <small>2</small>
  </div>
</section>

document.body.style.margin = '0'
document.documentElement.style.setProperty('--ui-scale', '1')

render(
  <div class="workspace" style="display:grid;grid-template-columns:239px 4px minmax(0,1fr) 40px;grid-template-rows:0 100dvh">
    <div class="sidebar" style="display:flex;flex-direction:column;background:var(--panel)">
      <div class="project-tree" style="flex:1;min-height:0;overflow:auto">
      <section class="sidebar-project-list sidebar-project-bucket" data-group-id="g1">
        <header><span class="bucket-chevron" aria-hidden="true">▾</span><span class="bucket-name">Active work</span>
          <button class="bucket-rename" aria-label="Rename group Active work">✎</button></header>
        {projectRow('swe-mux', true)}
        {projectRow('continuity')}
      </section>
      <section class="sidebar-project-list sidebar-project-bucket collapsed" data-group-id="g2">
        <header><span class="bucket-chevron" aria-hidden="true">▸</span><span class="bucket-name">Archived</span>
          <span class="bucket-count-badge" title="3 Projects in this group">3</span>
          <span class="bucket-collapsed-badge activity-working"><i aria-hidden="true"/>2</span>
          <button class="bucket-rename" aria-label="Rename group Archived">✎</button></header>
      </section>
      <div class="sidebar-project-list sidebar-ungrouped-projects" data-group-id="">
        {projectRow('scratch')}
        {projectRow('dotfiles')}
      </div>
      <section class="sidebar-project-list sidebar-project-bucket" data-group-id="g3">
        <header><span class="bucket-chevron" aria-hidden="true">▾</span><span class="bucket-name">Experiments</span>
          <button class="bucket-rename" aria-label="Rename group Experiments">✎</button></header>
        {projectRow('orca')}
      </section>
      </div>
    </div>
  </div>,
  document.querySelector('#root')!,
)
