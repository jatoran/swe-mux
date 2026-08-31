import assert from 'node:assert/strict'
import test from 'node:test'
import {
  alphabetizedPluginProjects, projectPluginPanes, selectedPluginProject,
  type InstalledPlugin,
} from '../src/pluginCatalog.ts'

const projects=[
  {id:'z',name:'Zulu'},
  {id:'a2',name:'alpha'},
  {id:'a1',name:'Alpha'},
]

test('plugin Project choices are alphabetical with a stable id tie-break',()=>{
  assert.deepEqual(alphabetizedPluginProjects(projects).map(project=>project.id),['a1','a2','z'])
})

test('the focused Project is the initial plugin target and an explicit choice survives refresh',()=>{
  assert.equal(selectedPluginProject(projects,'z',''),'z')
  assert.equal(selectedPluginProject(projects,'z','a2'),'a2')
  assert.equal(selectedPluginProject(projects,'missing',''),'a1')
})

test('the Project launcher exposes only enabled Project-scoped panes',()=>{
  const plugin=(id:string,enabled:boolean,contexts:string[]):InstalledPlugin=>({
    id,name:id,version:'1',enabled,lifecycle:'enabled',source_kind:'link',source_ref:'',requested_ref:'',selected_ref:'',resolved_ref:'',
    diagnostic:'',approval_current:true,config_dir:'',state_dir:'',manifest:{
      id,name:id,version:'1',description:'',author:'',license:'',homepage:'',permissions:[],requires:[],
      runtime_requirements:[],actions:[],events:[],startup:[],link_handlers:[],
      panes:[{id:'pane',title:id,description:'',contexts,placement:'tab',command:{command:[],cwd:'.',timeout_seconds:60}}],
    },
  })
  assert.deepEqual(projectPluginPanes([
    plugin('enabled',true,['project']),
    plugin('global',true,['global']),
    plugin('disabled',false,['project']),
  ]).map(item=>item.pluginId),['enabled'])
})
