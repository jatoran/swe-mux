import assert from 'node:assert/strict'
import test from 'node:test'
import {
  projectPluginPanes, type InstalledPlugin,
} from '../src/pluginCatalog.ts'

test('the Project launcher exposes only enabled Project-scoped panes',()=>{
  const plugin=(id:string,enabled:boolean,contexts:string[]):InstalledPlugin=>({
    id,name:id,version:'1',enabled,lifecycle:'enabled',source_kind:'link',source_ref:'',requested_ref:'',selected_ref:'',resolved_ref:'',
    diagnostic:'',approval_current:true,config_dir:'',state_dir:'',running_panes:[],update_check:null,staged_update:null,manifest:{
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
