export type PluginCommandSpec={command:string[];cwd:string;timeout_seconds:number}

export type PluginActionContribution={
  id:string;title:string;description:string;contexts:string[];command:PluginCommandSpec
}

export type PluginPaneContribution=PluginActionContribution&{
  placement:'tab'|'split'|'popup'
}

export type PluginManifest={
  id:string;name:string;version:string;description:string;author:string;license:string;homepage:string
  permissions:string[];requires:string[];runtime_requirements:string[]
  actions:PluginActionContribution[];panes:PluginPaneContribution[]
  events:Array<{id:string;on:string}>;startup:Array<{id:string}>
  link_handlers:Array<{id:string;title:string;pattern:string}>
}

export type InstalledPlugin={
  id:string;name:string;version:string;enabled:boolean;lifecycle:string
  source_kind:string;source_ref:string;resolved_ref:string;diagnostic:string
  approval_current:boolean;config_dir:string;state_dir:string;manifest:PluginManifest|null
}

export type PluginCatalogue={
  execution_enabled:boolean;host_capabilities:string[];plugins:InstalledPlugin[]
}

export type PluginProject={id:string;name:string}

export function alphabetizedPluginProjects(projects:readonly PluginProject[]):PluginProject[]{
  return [...projects].sort((left,right)=>
    left.name.localeCompare(right.name,undefined,{sensitivity:'base'})||left.id.localeCompare(right.id))
}

export function selectedPluginProject(
  projects:readonly PluginProject[],
  focusedProjectId:string,
  currentProjectId:string,
):string{
  if(currentProjectId&&projects.some(project=>project.id===currentProjectId))return currentProjectId
  if(focusedProjectId&&projects.some(project=>project.id===focusedProjectId))return focusedProjectId
  return alphabetizedPluginProjects(projects)[0]?.id||''
}

export function projectPluginPanes(plugins:readonly InstalledPlugin[]){
  return plugins.flatMap(plugin=>(plugin.enabled&&plugin.manifest?plugin.manifest.panes:[])
    .filter(pane=>pane.contexts.includes('project'))
    .map(pane=>({pluginId:plugin.id,pluginName:plugin.name,pane})))
    .sort((left,right)=>left.pluginName.localeCompare(right.pluginName)||left.pane.title.localeCompare(right.pane.title))
}

