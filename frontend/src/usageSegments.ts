import { t } from './localization/index.ts'
export type UsageSegment = 'overview' | 'agents' | 'quota' | 'activity' | 'automation'
export type UsageSegmentDescriptor = {id:UsageSegment;label:string;title:string;heading:string;footer:string}
export const USAGE_SEGMENTS: UsageSegmentDescriptor[] = [
  {id:'overview',get label() { return t("usageSegments.d4b1ea5708") },get title() { return t("usageSegments.e0f86baddd") },get heading() { return t("usageSegments.d96a9b9cc5") },get footer() { return t("usageSegments.696e247dd9") }},
  {id:'agents',get label() { return t("usageSegments.33d92926a4") },get title() { return t("usageSegments.5e521c7997") },get heading() { return t("usageSegments.d96a9b9cc5") },get footer() { return t("usageSegments.4241db8103") }},
  {id:'quota',get label() { return t("usageSegments.6c105cae1a") },get title() { return t("usageSegments.b6c2543d1f") },get heading() { return t("usageSegments.d96a9b9cc5") },get footer() { return t("usageSegments.da6beb9f9f") }},
  {id:'activity',get label() { return t("usageSegments.38da1505ca") },get title() { return t("usageSegments.e2e713633c") },get heading() { return t("usageSegments.d96a9b9cc5") },get footer() { return t("usageSegments.cd54a9bfae") }},
  {id:'automation',get label() { return t("usageSegments.d909750b1b") },get title() { return t("usageSegments.cdb7d1397d") },get heading() { return t("usageSegments.d96a9b9cc5") },get footer() { return t("usageSegments.c4c2962918") }},
]
