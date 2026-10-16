export const planStages = [
  { id: 'parse', title: '理解你的出行需求', description: '核对日期、起点、预算和个人偏好' },
  { id: 'retrieve', title: '寻找合适的地点', description: '从杭州地点库召回符合条件的候选' },
  { id: 'select', title: '挑选这次值得去的地方', description: '结合偏好与必去地点，确定游览候选' },
  { id: 'schedule', title: '排好每天的游览顺序', description: '计算地点间交通与可用游览时段' },
  { id: 'meals', title: '安排沿途餐饮与夜间活动', description: '比较餐厅，检查夜游是否适合加入' },
  { id: 'validate', title: '核对路线和时间', description: '检查营业时间、预算、步行与行程完整性' },
  { id: 'finalize', title: '整理你的杭州行记', description: '汇总行程、交通数据与待确认事项' },
] as const

export type PlanStage = typeof planStages[number]['id'] | 'repair' | 'refine'
export interface PlanProgressEvent {
  type: 'progress'
  stage: PlanStage
  status: 'running' | 'completed'
  round: number
  detail: string
}
export interface PlanProgressState {
  active: PlanStage | null
  completed: string[]
  details: Record<string, string>
  /** 当前步骤执行途中的实时数量，如“已算 18 / 30 段交通”；步骤切换时清空 */
  live: string
  round: number
}
export const emptyProgress = (): PlanProgressState => ({ active: null, completed: [], details: {}, live: '', round: 0 })

export function advanceProgress(state: PlanProgressState, event: PlanProgressEvent): PlanProgressState {
  const next = { ...state, completed: [...state.completed], details: { ...state.details }, round: event.round }
  if (event.status === 'running') {
    // 同一步骤执行途中会反复上报进度，这时只换实时数量那行，不重置步骤状态
    if (state.active === event.stage && event.detail) { next.live = event.detail; return next }
    next.active = event.stage
    next.live = ''
    if (event.stage === 'repair') {
      next.completed = next.completed.filter(stage => stage !== 'validate')
      delete next.details.repair
    }
    const index = planStages.findIndex(stage => stage.id === event.stage)
    if (index >= 0) {
      const repeated = planStages.slice(index).map(stage => stage.id as string)
      next.completed = next.completed.filter(stage => !repeated.includes(stage))
      for (const stage of repeated) delete next.details[stage]
    }
  } else {
    next.live = ''
    next.details[event.stage] = event.detail
    if (event.stage !== 'repair' && event.stage !== 'refine' && !next.completed.includes(event.stage)) next.completed.push(event.stage)
  }
  return next
}
