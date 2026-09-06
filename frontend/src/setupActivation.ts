import { api } from './api.ts'
import type { OnboardingState, SaveOnboarding, SetupDraft } from './onboarding.ts'

/** Optimistic launch placeholders are not proof that a session ever started. */
export const setupHasStartedSession=(sessions:readonly {id:string}[])=>sessions.some(session=>!session.id.startsWith('pending-'))

export function wantsSetupModels(draft:SetupDraft):boolean {
  const defaults=draft.tier==='automations'
  return (draft.overrides?.automation_enabled??defaults)||(draft.overrides?.scan_timeline_enabled??defaults)
}
export async function completeSetupModels(state:OnboardingState,save:SaveOnboarding) {
  if(state.draft.model_features_pending){
    const overrides=Object.fromEntries(Object.entries(state.draft.overrides||{}).filter(([key])=>key==='automation_enabled'||key==='scan_timeline_enabled'))
    await api('POST','/api/onboarding/features/activate',{features:['automations'],overrides},{timeoutMs:15000})
  }
  return save(current=>({draft:{...current.draft,model_features_pending:false},completed:[...new Set([...current.completed,'provider'])]}))
}
