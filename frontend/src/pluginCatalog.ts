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
  source_kind:string;source_ref:string;requested_ref:string;selected_ref:string;resolved_ref:string;diagnostic:string
  approval_current:boolean;config_dir:string;state_dir:string;manifest:PluginManifest|null
  running_panes:Array<{session_id:string;project_id:string;pane_id:string;placement:string}>
  update_check:{
    status:'available'|'current'|'pinned'|'unavailable'|'staged';checked_at:number
    current_ref?:string;available_ref?:string;available_version?:string;channel?:string;diagnostic?:string
  }|null
  staged_update:{
    version:string;current_version:string;selected_ref:string;resolved_ref:string
    permissions_added:string[];permissions_removed:string[]
    capabilities_added:string[];capabilities_removed:string[]
    authority_changed:boolean;diagnostic:string;created_at:number
  }|null
}

export type PluginCatalogue={
  execution_enabled:boolean;host_capabilities:string[];development_root:string;plugins:InstalledPlugin[]
}

export type PluginDevelopmentCandidate={
  path:string;id:string;name:string;version:string;description:string
  diagnostic:string;linked:boolean;conflict:boolean
}

export type PluginDevelopmentScan={
  root:string;exists:boolean;candidates:PluginDevelopmentCandidate[];truncated:boolean;diagnostic?:string
}

export function projectPluginPanes(plugins:readonly InstalledPlugin[]){
  return plugins.flatMap(plugin=>(plugin.enabled&&plugin.manifest?plugin.manifest.panes:[])
    .filter(pane=>pane.contexts.includes('project'))
    .map(pane=>({pluginId:plugin.id,pluginName:plugin.name,pane})))
    .sort((left,right)=>left.pluginName.localeCompare(right.pluginName)||left.pane.title.localeCompare(right.pane.title))
}
