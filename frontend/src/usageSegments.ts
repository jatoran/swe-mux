export type UsageSegment = 'overview' | 'agents' | 'quota' | 'activity' | 'automation'
export type UsageSegmentDescriptor = {id:UsageSegment;label:string;title:string;heading:string;footer:string}
export const USAGE_SEGMENTS: UsageSegmentDescriptor[] = [
  {id:'overview',label:'Overview',title:'Capacity, consumption, and agent activity',heading:'Usage & activity',footer:'Each figure keeps its own source, period, and cost basis.'},
  {id:'agents',label:'Agent usage',title:'Historical tokens and estimated cost',heading:'Usage & activity',footer:'Transcript estimates are not subscription bills or account-specific quota.'},
  {id:'quota',label:'Quota',title:'Account capacity and utilization',heading:'Usage & activity',footer:'Provider quota percentages are separate from transcript token totals.'},
  {id:'activity',label:'Activity',title:'Runs, tools, checks, and context',heading:'Usage & activity',footer:'Recorded activity is evidence of execution, not proof of task completion.'},
  {id:'automation',label:'Automation',title:'Metered costs by feature and rule',heading:'Usage & activity',footer:'Unpriced calls make a reported cost a lower bound.'},
]
