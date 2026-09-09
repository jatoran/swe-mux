import { render } from 'preact'
import { useState } from 'preact/hooks'
import { ProjectCreateDialog } from '../../src/ProjectCreateDialog'
import type { Project } from '../../src/types'
import '../../src/deviceMode'
import '../../src/style.css'

function Host(){
  const [open,setOpen]=useState(true)
  const [project,setProject]=useState<Project|null>(null)
  const [error,setError]=useState('')
  const groups=new URLSearchParams(location.search).has('groups')?[{id:'g1',name:'Work',position:0}]:[]
  return <main>
    {open&&<ProjectCreateDialog groups={groups} onClose={()=>setOpen(false)} onCreated={value=>{setProject(value);setOpen(false)}} onSetupComplete={(_,result)=>setError(result.errors.map(item=>item.error).join(' · '))}/>}
    {project&&<h1>Workspace: {project.name}</h1>}
    {error&&<p role="alert">{error}</p>}
  </main>
}
render(<Host/>,document.querySelector('#root')!)
