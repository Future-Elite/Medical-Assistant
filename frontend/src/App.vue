<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import Icon from './components/Icon.vue'
import TrendChart from './components/TrendChart.vue'
import ModelSelect from './components/ModelSelect.vue'
import BatchWorkspace from './components/BatchWorkspace.vue'
import './batch.css'
import PhysicianRecord from './components/PhysicianRecord.vue'
import { downloadJob, post, request } from './api'
import { formatTime, statusLabels } from './utils'

const scenarios = ref([]), health = ref(null), history = ref([]), job = ref(null)
const guidelineCatalog = ref(null), models = ref([]), activeBatchId = ref(null)
const viewTitle = computed(() => ({workbench:'轨迹工作台',batch:'批量生成',history:'生成记录'}[view.value]))
const visibleScenarios = computed(() => scenarios.value.filter(s => s.population === 'neonatal'))
const view = ref('workbench'), selectedStep = ref(0), measurementIndex = ref(0)
const submitting = ref(false), notice = ref(''), error = ref(''), followLatest = ref(true)
const showPlan = ref(false), preview = ref([]), previewLoading = ref(false)
const timelineRail = ref(null)
const seedFieldLabels = {'patient.gestational_weeks':'出生胎龄（周）','patient.birth_weight_g':'出生体重（g）','patient.sex':'性别','patient.postnatal_age_hours':'初始生后年龄（小时）','patient.gestational_days':'胎龄余数（天）','patient.weight_g':'当前体重（g）','initial_state.heart_rate':'心率（bpm）','initial_state.respiratory_rate':'呼吸频率（次/min）','initial_state.temperature':'体温（°C）','initial_state.ph':'血气pH','initial_state.paco2':'血气PaCO₂（mmHg）','initial_state.glucose':'血糖（mmol/L）','initial_state.spo2':'血氧饱和度（%）','initial_state.fio2':'吸入氧浓度','initial_state.cpap_cm_h2o':'CPAP压力（cmH₂O）','initial_state.respiratory_support':'呼吸支持方式','initial_state.spontaneous_breathing':'自主呼吸','initial_state.recurrent_apnea':'反复呼吸暂停','initial_state.suspected_pathogen':'模拟病原判断','initial_state.bacterial_pneumonia_supported':'细菌感染支持证据'}
watch(view, async()=>{ await nextTick(); window.scrollTo({top:0,behavior:'auto'}) })
const today = new Date()
const apiDocsUrl = import.meta.env.DEV ? 'http://127.0.0.1:8000/docs' : '/docs'
const dateString = `${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-${String(today.getDate()).padStart(2,'0')}`
const form = reactive({
  scenario_id: 'neonatal_pneumonia', backend: 'api', model: 'Qwen3.8-27B', steps: 10, interval_hours: 3, start_time: `${dateString}T08:00`, care_setting: 'nicu',
  patient: { name: '新生儿 001', age: null, sex: '男', postnatal_age_hours: 48, gestational_weeks: 38, gestational_days: 2,
    birth_weight_g: 3200, weight_g: 3100, chief_complaint: '生后呼吸急促、呻吟，伴喂养减少',
    primary_diagnosis: '新生儿感染性肺炎（疑似）', history: '生后出现呼吸急促，需鉴别呼吸窘迫病因。',
    perinatal_history: '母亲胎膜早破约 20 小时，GBS 筛查情况需核实。', allergies: '过敏史未明确' },
  initial_state: { spo2: 90, respiratory_rate: 72, fio2: .30, cpap_cm_h2o: 6, respiratory_support: 'oxygen',
    spontaneous_breathing: true, recurrent_apnea: false, infection_suspected: true, suspected_pathogen: 'bacterial', bacterial_pneumonia_supported: false },
})
const scenario = computed(() => scenarios.value.find(s => s.id === (job.value?.request.scenario_id ?? form.scenario_id)) || scenarios.value[0])
const measurements = computed(() => scenario.value?.measurements || [])
const currentMeasurement = computed(() => measurements.value[measurementIndex.value] || measurements.value[0])
const steps = computed(() => job.value?.trajectory.steps || [])
const totalSteps = computed(() => job.value?.total_steps || form.steps || 10)
const progress = computed(() => steps.value.length / totalSteps.value * 100)
const step = computed(() => steps.value[selectedStep.value])
const physicianRecord = computed(() => step.value?.physician_record)
const isNeonatal = computed(() => form.scenario_id.startsWith('neonatal_'))
const sourceManifest = computed(() => job.value ? job.value.trajectory.guidelines || null : guidelineCatalog.value)
const events = computed(() => step.value?.events || [])
const numericEvents = computed(() => events.value.filter(e => e.action.value_type === 'number'))
const interventions = computed(() => events.value.filter(e => e.action.kind === 'intervention'))
const note = computed(() => events.value.find(e => e.action.concept === 'web:summary')?.value)
const isRunning = computed(() => ['queued', 'running'].includes(job.value?.status))
const isCompleted = computed(() => job.value?.status === 'completed' && steps.value.length === totalSteps.value)
const displayedPatient = computed(() => job.value?.request.patient || form.patient)
const eventCount = computed(() => steps.value.reduce((n, s) => n + s.events.length, 0))
let timer, controller, selection = 0, notificationTimer

watch([selectedStep, () => steps.value.length], async () => {
  await nextTick()
  const rail = timelineRail.value
  const target = rail?.querySelector('button.selected')
  if (!target) return
  const relativeX = target.getBoundingClientRect().left - rail.getBoundingClientRect().left
  rail.scrollTo({ left: rail.scrollLeft + relativeX - (rail.clientWidth - target.clientWidth) / 2, behavior: 'smooth' })
})

function notify(message) {
  notice.value = message
  clearTimeout(notificationTimer)
  notificationTimer = setTimeout(() => notice.value = '', 3500)
}
function remember(id) { try { id ? localStorage.setItem('ehrworld-job', id) : localStorage.removeItem('ehrworld-job') } catch {} }
function stopPolling() { clearTimeout(timer); controller?.abort(); selection++ }
async function refreshHistory() { history.value = (await request('/generations')).items }
function applyJob(result) {
  const grew = result.completed_steps > (job.value?.completed_steps || 0)
  job.value = result
  if (followLatest.value && grew) selectedStep.value = Math.max(0, result.completed_steps - 1)
  if (selectedStep.value >= result.completed_steps) selectedStep.value = Math.max(0, result.completed_steps - 1)
  remember(result.id)
}
async function watchJob(id, token) {
  controller = new AbortController()
  try {
    const result = await request(`/generations/${id}`, { signal: controller.signal })
    if (token !== selection) return
    applyJob(result)
    error.value = ''
    if (['queued', 'running'].includes(result.status)) timer = setTimeout(() => watchJob(id, token), 900)
    else refreshHistory().catch(() => {})
  } catch (e) {
    if (e.name === 'AbortError' || token !== selection) return
    error.value = '进度连接已中断，正在尝试重新连接。'
    timer = setTimeout(() => watchJob(id, token), 2500)
  }
}
async function selectJob(id) {
  stopPolling()
  const token = selection
  try {
    const result = await request(`/generations/${id}`)
    if (token !== selection) return
    job.value = null
    followLatest.value = true
    applyJob(result)
    activeBatchId.value = result.batch_id || null
    Object.assign(form, { ...result.request, patient: { ...result.request.patient } })
    measurementIndex.value = 0
    selectedStep.value = Math.max(0, result.completed_steps - 1)
    view.value = 'workbench'
    await nextTick(); window.scrollTo({top:0,behavior:'auto'})
    if (['queued', 'running'].includes(result.status)) watchJob(id, token)
  } catch (e) { error.value = e.message; remember(null) }
}
function rememberBatch(id) {
  activeBatchId.value = id
  try { id ? localStorage.setItem('ehrworld-batch',id) : localStorage.removeItem('ehrworld-batch') } catch {}
}
function openBatchPatient(id,batchId) { rememberBatch(batchId); selectJob(id) }
function selectScenario(id) {
  const item = scenarios.value.find(s => s.id === id)
  if (!item) return
  form.scenario_id = id
  form.patient = { ...item.default_patient }
  if (item.default_initial_state) form.initial_state = { ...item.default_initial_state }
  form.interval_hours = item.default_interval_hours || 3
  preview.value = []
  showPlan.value = false
  if (!job.value) measurementIndex.value = 0
}
function payload(backend) {
  return { patient: { ...form.patient }, scenario_id: form.scenario_id, backend: backend || form.backend,
    initial_state: { ...form.initial_state }, care_setting: form.care_setting,
    interval_hours: form.interval_hours, start_time: form.start_time, steps: form.steps, model: form.model }
}
async function generate(backend) {
  submitting.value = true
  error.value = ''
  try {
    const result = await post('/generations', payload(backend))
    activeBatchId.value = null
    stopPolling()
    job.value = null
    selectedStep.value = 0
    followLatest.value = true
    measurementIndex.value = 0
    applyJob(result)
    view.value = 'workbench'
    watchJob(result.id, selection)
    await refreshHistory()
  } catch (e) { error.value = e.message || '无法连接后端服务，请确认服务已启动。' }
  finally { submitting.value = false }
}
async function retry() {
  if (!job.value) return
  submitting.value = true
  try {
    const result = await post(`/generations/${job.value.id}/retry`, {})
    stopPolling(); job.value = null; followLatest.value = true; applyJob(result)
    selectedStep.value = 0; watchJob(result.id, selection)
    await refreshHistory()
  } catch (e) { error.value = e.message }
  finally { submitting.value = false }
}
async function exportData(format) {
  try { await downloadJob(job.value.id, format); notify(`${format.toUpperCase()} 文件已导出`) }
  catch (e) { error.value = e.message }
}
async function previewPlan() {
  showPlan.value = !showPlan.value
  if (!showPlan.value) return
  previewLoading.value = true
  try { preview.value = (await post('/plans/preview', payload())).steps }
  catch (e) { error.value = e.message }
  finally { previewLoading.value = false }
}
function newPatient() {
  stopPolling(); job.value = null; remember(null); selectedStep.value = 0; measurementIndex.value = 0
  view.value = 'workbench'; error.value = ''; preview.value = []; showPlan.value = false
  activeBatchId.value = null; form.steps = 10; form.source_seed = null
  selectScenario('neonatal_pneumonia')
}
function patientLabel(p) {
  if (p.age > 0) return `${p.age} 岁 · ${p.sex}`
  const hours = p.postnatal_age_hours || 0
  return `生后 ${Math.floor(hours / 24)} 天 ${Number((hours % 24).toFixed(1))} 小时 · ${p.sex}`
}
function chooseStep(index) { selectedStep.value = index; followLatest.value = false }
function delta(event) {
  if (!selectedStep.value) return '首次观测'
  const previous = steps.value[selectedStep.value - 1]?.events.find(e => e.action.concept === event.action.concept)?.value
  if (typeof previous !== 'number') return '首次观测'
  const difference = Number((event.value - previous).toFixed(2))
  return difference === 0 ? '较上一步持平' : `${difference > 0 ? '+' : ''}${difference} 较上一步`
}
onMounted(async () => {
  try {
    const [h, s, g, m] = await Promise.all([request('/health'), request('/scenarios'), request('/guidelines'), request('/models')])
    models.value = m.items; form.model = m.default_model
    try { activeBatchId.value = localStorage.getItem('ehrworld-batch') } catch {}
    health.value = h; scenarios.value = [...s.items, ...(s.legacy_items || [])]; guidelineCatalog.value = g
    await refreshHistory()
    let saved
    try { saved = localStorage.getItem('ehrworld-job') } catch {}
    if (saved) await selectJob(saved)
  } catch { error.value = '暂时无法连接后端服务，请确认 FastAPI 服务已在本机启动。' }
})
onBeforeUnmount(() => { stopPolling(); clearTimeout(notificationTimer) })
</script>

<template>
  <div class="app-layout">
    <aside class="sidebar">
      <a class="brand" href="/" aria-label="EHRWorld 首页"><span class="brand-mark"><Icon name="pulse" :size="26" /></span><span>EHR<span class="brand-light">World</span><small>PATIENT WORLD MODEL</small></span></a>
      <div class="workspace-label">研究工作空间 <span>LOCAL</span></div>
      <nav aria-label="主导航">
        <button aria-label="轨迹工作台" :class="{ active: view === 'workbench' }" @click="view = 'workbench'"><Icon name="grid" /> <span>轨迹工作台</span><i v-if="view === 'workbench'"></i></button>
        <button aria-label="批量生成" :class="{ active: view === 'batch' }" @click="view = 'batch'"><Icon name="layers"/><span>批量生成</span><i v-if="view === 'batch'"></i></button>
        <button aria-label="生成记录" :class="{ active: view === 'history' }" @click="view = 'history'; refreshHistory().catch(() => error = '任务记录加载失败。')"><Icon name="clock" /><span>生成记录</span><b>{{ history.length }}</b></button>
      </nav>
      <div v-if="view !== 'batch'" class="sidebar-section"><div class="eyebrow">当前任务</div><div class="side-patient"><span class="avatar"><Icon name="user" /></span><div><strong>{{ displayedPatient.name }}</strong><small>{{ patientLabel(displayedPatient) }}</small></div></div><div class="mini-progress"><span :style="{ width: `${progress}%` }"></span></div><div class="side-progress-text"><span>{{ job ? statusLabels[job.status] : '等待开始' }}</span><b>{{ steps.length }} / {{ totalSteps }}</b></div></div>
      <div class="sidebar-bottom"><div class="research-note"><Icon name="layers" /><strong>从状态，到下一刻。</strong><p>在连续的观察与干预中，<br>构建患者的时间轨迹。</p><span>EHRWorld · 研究工具</span></div><a :href="apiDocsUrl" target="_blank" rel="noreferrer"><Icon name="book" :size="17" /><span>接口文档</span><Icon name="chevron" :size="14" /></a></div>
    </aside>

    <div class="main-shell">
      <header class="topbar"><div class="breadcrumb">研究工作空间 <Icon name="chevron" :size="13" /><strong>{{ viewTitle }}</strong></div><div class="topbar-right"><button class="mobile-nav" aria-label="批量生成" @click="view='batch'"><Icon name="layers" :size="18"/></button><button class="mobile-nav" :aria-label="view === 'workbench' ? '查看生成记录' : '返回轨迹工作台'" @click="view = view === 'workbench' ? 'history' : 'workbench'; refreshHistory().catch(() => {})"><Icon :name="view === 'workbench' ? 'clock' : 'grid'" :size="18" /></button><span class="service-status" :class="{ online: health }"><i></i>{{ health ? '服务在线' : '连接服务中' }}</span><span class="research-badge">RESEARCH</span><span class="top-avatar">EW</span></div></header>
      <main>
        <div class="page-heading"><div><div class="eyebrow">NEONATAL TRAJECTORY STUDIO</div><h1>{{ view === 'workbench' ? '构建新生儿诊疗时间轨迹' : view === 'batch' ? '从初始患者，到一批时间轨迹' : '每一次生成，都有迹可循' }}</h1><p>{{ view === 'workbench' ? '结合新生儿肺炎 / RDS 指南，记录每一步医生动作与患者观测结果。' : view === 'batch' ? '从 test.json 选择10名不同患者，按3 / 5 / 2分组生成不同长度的病历。' : '查看已保存的患者轨迹、生成进度与模型运行记录。' }}</p></div><button class="button secondary new-button" @click="newPatient"><Icon name="plus" :size="17" />新建轨迹</button></div>
        <div v-if="error" class="alert" role="alert"><Icon name="warning" :size="19" /><span>{{ error }}</span><button @click="error = ''" aria-label="关闭提示">×</button></div>

        <BatchWorkspace v-if="view === 'batch'" :models="models" :initial-batch-id="activeBatchId" :default-model="form.model" @open-patient="openBatchPatient" @selected-batch="rememberBatch" @updated="refreshHistory().catch(() => {})"/>
        <template v-if="view === 'workbench'">
          <button v-if="job?.batch_id" class="button secondary batch-back" @click="rememberBatch(job.batch_id); view='batch'"><Icon name="arrow" :size="16"/>返回10人批次 · 患者 {{ job.batch_index }} · 目标 {{ totalSteps }}步</button>
          <div class="studio-layout">
            <section class="card patient-config">
              <div class="section-title"><span class="section-icon"><Icon name="user" :size="19" /></span><div><h2>新生儿画像</h2><p>胎龄、日龄与围产背景</p></div><span class="step-label">01</span></div>
              <form @submit.prevent="generate()">
                <fieldset :disabled="isRunning || submitting">
                  <label class="field-label">研究场景</label>
                  <div class="scenario-switch"><button v-for="item in visibleScenarios" :key="item.id" type="button" :class="{ selected: form.scenario_id === item.id }" @click="selectScenario(item.id)">{{ item.name }}</button></div>
                  <label class="field">患者标识<input v-model="form.patient.name" maxlength="60" required /></label>
                  <div class="field-row"><label v-if="isNeonatal" class="field">初始生后年龄（小时）<input v-model.number="form.patient.postnatal_age_hours" type="number" min="0" max="671.99" step="0.5" required /></label><label v-else class="field">年龄（历史场景）<input v-model.number="form.patient.age" type="number" min="18" max="110" required /></label><label class="field">性别<select v-model="form.patient.sex"><option>男</option><option>女</option><option>其他</option><option>未知</option></select></label></div>
                  <template v-if="isNeonatal"><div class="field-row"><label class="field">出生胎龄（周）<input v-model.number="form.patient.gestational_weeks" type="number" min="22" :max="form.scenario_id === 'neonatal_rds' ? 36 : 42" required /></label><label class="field">胎龄余数（天）<input v-model.number="form.patient.gestational_days" type="number" min="0" max="6" required /></label></div><div class="field-row"><label class="field">出生体重（g）<input v-model.number="form.patient.birth_weight_g" type="number" min="200" max="6500" required /></label><label class="field">当前体重（g）<input v-model.number="form.patient.weight_g" type="number" min="200" max="6500" required /></label></div></template>
                  <label class="field">主要诊断<input v-model="form.patient.primary_diagnosis" maxlength="300" required /></label>
                  <label class="field">主诉<textarea v-model="form.patient.chief_complaint" rows="2" maxlength="1200" required></textarea></label>
                  <label class="field">病情与既往史<textarea v-model="form.patient.history" rows="2" maxlength="2000"></textarea></label><label v-if="isNeonatal" class="field">围产史 / 母体感染风险<textarea v-model="form.patient.perinatal_history" rows="2" maxlength="2000"></textarea></label>
                  <details v-if="isNeonatal" class="advanced-fields initial-state"><summary>初始呼吸与感染状态 <Icon name="chevron" :size="14" /></summary><div class="field-row"><label class="field">初始 SpO₂（%）<input v-model.number="form.initial_state.spo2" type="number" min="50" max="100" step="1" /></label><label class="field">初始 FiO₂（0–1）<input v-model.number="form.initial_state.fio2" type="number" min="0.21" max="1" step="0.01" /></label></div><div class="field-row"><label class="field">呼吸频率（次/min）<input v-model.number="form.initial_state.respiratory_rate" type="number" min="10" max="140" /></label><label class="field">CPAP（cmH₂O）<input v-model.number="form.initial_state.cpap_cm_h2o" type="number" min="0" max="12" step="0.5" /></label></div><label class="field">初始呼吸支持<select v-model="form.initial_state.respiratory_support"><option value="room_air">空气下</option><option value="oxygen">氧疗</option><option value="cpap">CPAP</option><option value="nippv">NIPPV</option><option value="invasive">有创通气</option></select></label><label class="field">初始病原判断<select v-model="form.initial_state.suspected_pathogen"><option value="bacterial">怀疑细菌感染</option><option value="viral">病毒感染背景</option><option value="undetermined">病原未定</option></select></label><label class="field">模拟救治场所<select v-model="form.care_setting"><option value="nicu">具备新生儿重症救治能力</option><option value="primary">基层机构（需评估转诊）</option></select></label><label class="check-field"><input v-model="form.initial_state.spontaneous_breathing" type="checkbox" />存在自主呼吸</label><label class="check-field"><input v-model="form.initial_state.recurrent_apnea" type="checkbox" />存在反复呼吸暂停</label><label class="check-field"><input v-model="form.initial_state.infection_suspected" type="checkbox" />初始有感染疑虑</label><label class="check-field"><input v-model="form.initial_state.bacterial_pneumonia_supported" type="checkbox" />已有细菌性肺炎支持证据</label></details>
                  <details class="advanced-fields"><summary>过敏史与起始时间 <Icon name="chevron" :size="14" /></summary><label class="field">过敏史<input v-model="form.patient.allergies" maxlength="300" /></label><label class="field">轨迹起始时间<input v-model="form.start_time" type="datetime-local" required /></label></details>
                  <div class="form-divider"></div>
                  <div class="subheading"><Icon name="clock" :size="16" />生成设置<span>{{ form.steps }} 步轨迹</span></div>
                  <div class="field-row"><label class="field">步长间隔<select v-model.number="form.interval_hours"><option :value="1">1 小时</option><option :value="3">3 小时</option><option :value="6">6 小时</option><option :value="12">12 小时</option><option :value="24">24 小时</option></select></label><label class="field">生成模式<select v-model="form.backend"><option value="api">API 模型</option><option value="demo">离线演示</option></select></label></div>
                  <ModelSelect v-model="form.model" :models="models"/>
                  <label class="field">目标步数<select v-model.number="form.steps"><option v-for="n in 10" :key="n" :value="n">{{ n }} 步</option></select></label>
                  <div class="generation-summary"><span>轨迹覆盖时长</span><strong>{{ form.interval_hours * (form.steps - 1) }}<small> 小时</small></strong></div>
                  <button class="button primary generate-button" type="submit" :disabled="!health || isRunning || submitting"><span v-if="isRunning || submitting" class="spinner"></span><Icon v-else name="spark" :size="18" />{{ isRunning ? `正在生成 ${steps.length} / ${totalSteps}` : submitting ? '正在提交' : '生成患者轨迹' }}<Icon v-if="!isRunning && !submitting" name="arrow" :size="17" /></button>
                  <button class="demo-link" type="button" :disabled="!health || isRunning || submitting" @click="generate('demo')">先体验离线演示 <Icon name="arrow" :size="14" /></button>
                </fieldset>
              </form>
              <p class="form-footnote"><Icon name="info" :size="13" />生成内容为合成研究数据。</p>
            </section>

            <div class="results-column">
              <section class="card trajectory-overview">
                <div class="overview-top"><div class="patient-heading"><span class="patient-avatar"><Icon name="user" :size="23" /></span><div><h2>{{ displayedPatient.name }} <span>{{ patientLabel(displayedPatient) }}</span></h2><p>{{ displayedPatient.primary_diagnosis }}</p></div></div><span class="status-pill" :class="job?.status || 'idle'"><span v-if="isRunning" class="spinner"></span><Icon v-else-if="isCompleted" name="check" :size="13" />{{ job ? statusLabels[job.status] : '尚未生成' }}</span></div>
                <div class="overview-stats"><div><span>时间节点</span><strong>{{ String(steps.length).padStart(2, '0') }}<small> / {{ totalSteps }} 步</small></strong></div><div><span>时间跨度</span><strong>{{ (job?.request.interval_hours || form.interval_hours) * (totalSteps - 1) }}<small> 小时</small></strong></div><div><span>EHR 事件</span><strong>{{ String(eventCount).padStart(2, '0') }}<small> 条</small></strong></div><div><span>数据来源</span><strong class="source-name">{{ job?.request.backend === 'demo' ? '规则演示' : job ? '模型生成' : '待生成' }}</strong><small class="source-caption">{{ job?.request.backend === 'demo' ? '人工合成规则' : job?.model?.replace(/^api:/, '') || job?.request.model || form.model }}</small></div></div>
                <div class="overview-progress"><span :style="{ width: `${progress}%` }"></span></div>
              </section>

              <section v-if="job?.request.source_seed" class="card seed-provenance"><div class="panel-heading"><div><h2><Icon name="file" :size="18"/>初始记录来源</h2><p>test.json · 患者 {{ job.request.source_seed.source_patient_id }} · 记录 {{ job.request.source_seed.source_record_id }}</p></div></div><p>只使用该患者最早的单时点记录。下方时间轨迹为合成随访，不代表来源患者的真实后续病程。</p><details><summary>查看已导入字段与模拟假设</summary><div class="seed-fields"><strong>来源已记录的初始值</strong><p v-for="(path,key) in job.request.source_seed.observed_fields" :key="key">{{ seedFieldLabels[key] || key }}：{{ key.startsWith('patient.') ? job.request.patient[key.slice(8)] : job.request.initial_state[key.slice(14)] }}<small>{{ path }}</small></p><strong>来源未记录，使用的模拟假设</strong><p v-for="(item,key) in job.request.source_seed.assumptions" :key="key">{{ seedFieldLabels[key] || key }} = {{ item.value }}<small>{{ item.reason }}</small></p></div></details></section>
              <section v-if="sourceManifest" class="card guideline-panel"><div class="panel-heading"><div><h2><Icon name="book" :size="18" />本地指南依据</h2><p>医生动作附带适用条件及原文页码</p></div><span class="research-badge">NEONATAL</span></div><div class="guideline-sources"><a v-for="source in sourceManifest.sources" :key="source.id" :href="source.url" target="_blank" rel="noreferrer"><Icon name="file" :size="16" /><span>{{ source.id === 'pneumonia-2025' ? '新生儿感染性肺炎 · 2025' : '欧洲 RDS 2025 版 · 中文解读' }}<small>{{ !source.available ? '本地文件缺失' : source.reviewed ? '本地原文已核对' : '文件已变化，需复核' }}</small></span><Icon name="chevron" :size="13" /></a></div><p class="guideline-scope">依据已生成的历史动态选择动作；不把成人参数、RDS 排除感染后的停药规则或产房复苏参数直接套用于所有新生儿。</p></section>
              <div v-if="job?.error" class="job-error" role="alert"><Icon name="warning" :size="23" /><div><strong>本次轨迹未能完整生成</strong><p>{{ job.error.message }}</p><button class="text-action" :disabled="submitting" @click="retry">重新尝试</button><button class="text-action" :disabled="submitting" @click="generate('demo')">用当前画像运行离线演示</button></div></div>
              <div v-if="job?.request.backend === 'demo'" class="demo-banner"><Icon name="info" :size="15" />当前为离线规则演示，帮助检查流程；结果不来自大模型推理。</div>

              <section class="card trend-panel">
                <div class="panel-heading"><div><h2><Icon name="chart" :size="18" />指标变化趋势</h2><p>沿时间观察患者状态的连续变化</p></div><span class="chart-legend"><i></i>{{ currentMeasurement?.name || '患者指标' }}</span></div>
                <div class="metric-tabs" role="group" aria-label="选择趋势指标"><button v-for="(m, i) in measurements" :key="m.key" :class="{ active: measurementIndex === i }" @click="measurementIndex = i">{{ m.name }}<span v-if="measurementIndex === i">{{ m.unit }}</span></button></div>
                <TrendChart :total-steps="totalSteps" :steps="steps" :measurement="currentMeasurement" :selected="selectedStep" @select="chooseStep" />
                <div class="chart-foot"><span><i></i>{{ steps.length ? `${steps.length} 个已生成的时间节点` : '等待轨迹生成' }}</span><button @click="previewPlan"><Icon name="layers" :size="14" />{{ showPlan ? '收起观察计划' : `查看 ${totalSteps} 步观察计划` }}<Icon name="chevron" :size="13" /></button></div>
                <div v-if="showPlan" class="plan-preview"><p v-if="previewLoading">正在读取观察计划…</p><template v-else><p>首步依据初始状态安排；后续仅预览常规观察，实际干预待前一步生成结果后再决定。</p><div v-for="item in preview" :key="item.index"><b>T{{ String(item.index).padStart(2, '0') }}</b><span>{{ formatTime(item.timestamp, true) }}</span><small>{{ item.actions.filter(a => a.kind === 'inquiry').length }} 项检查 <em v-if="item.actions.some(a => a.kind === 'intervention')">· 含干预</em></small><details><summary>查看动作</summary><p v-for="action in item.actions" :key="action.action_id">{{ action.kind === 'inquiry' ? '检查' : '干预' }} · {{ action.name }}</p></details></div></template></div>
              </section>

              <section class="card ehr-panel">
                <div class="panel-heading"><div><h2><Icon name="file" :size="18" />EHR 时间轨迹</h2><p>选择时间节点，查看这一刻的完整记录</p></div><div class="export-buttons"><button :disabled="!isCompleted" @click="exportData('json')" title="导出完整 JSON"><Icon name="download" :size="15" />JSON</button><button :disabled="!isCompleted" @click="exportData('csv')" title="导出完整 CSV"><Icon name="download" :size="15" />CSV</button></div></div>
                <div ref="timelineRail" class="time-rail" :style="{gridTemplateColumns:`repeat(${totalSteps}, minmax(53px, 1fr))`}" aria-label="轨迹时间节点"><button v-for="n in totalSteps" :key="n" :disabled="n > steps.length" :aria-current="selectedStep === n - 1 && steps.length ? 'step' : undefined" :class="{ selected: selectedStep === n - 1 && steps.length, done: n <= steps.length }" @click="chooseStep(n - 1)"><span class="rail-dot"><Icon v-if="n <= steps.length && n - 1 !== selectedStep" name="check" :size="11" /><i v-else></i></span><strong>T{{ String(n).padStart(2, '0') }}</strong><small>{{ steps[n - 1] ? formatTime(steps[n - 1].timestamp, true) : `+${(n-1) * (job?.request.interval_hours || form.interval_hours)}h` }}</small></button></div>
                <div v-if="!step" class="empty-ehr"><span class="empty-illustration"><Icon name="file" :size="34" /><i><Icon name="plus" :size="14" /></i></span><h3>{{ isRunning ? '正在构建患者的第一个时间节点' : '每一条轨迹，都从患者画像开始' }}</h3><p>{{ isRunning ? '模型生成需要一些时间，完成的步骤会自动出现在这里。' : '完成左侧配置后开始生成，或先体验一条离线演示轨迹。' }}</p><button v-if="!isRunning" class="text-action" :disabled="!health || submitting" @click="generate('demo')">查看离线演示 <Icon name="arrow" :size="14" /></button><span v-else class="loading-dots">● ● ●</span></div>
                <div v-else class="step-detail">
                  <div class="step-heading"><div><span>T{{ String(selectedStep + 1).padStart(2, '0') }}</span><h3>{{ formatTime(step.timestamp, true) }} <small>第 {{ selectedStep + 1 }} 次观察</small></h3></div><span class="record-count">{{ events.length }} 条事件</span></div>
                  <div v-if="displayedPatient.age == null || displayedPatient.age === 0" class="neonatal-summary">初始胎龄 {{ displayedPatient.gestational_weeks }}+{{ displayedPatient.gestational_days }} 周 · 出生体重 {{ displayedPatient.birth_weight_g }}g · 当前 {{ displayedPatient.weight_g }}g<span v-if="physicianRecord"> · 本步生后 {{ physicianRecord.postnatal_age_hours }} 小时</span></div><div class="observations"><div v-for="event in numericEvents" :key="event.action.action_id" class="observation"><span>{{ event.action.name }}</span><strong>{{ event.value }}<small>{{ event.action.unit }}</small></strong><em>{{ delta(event) }}</em></div></div>
                  <PhysicianRecord :record="physicianRecord" :events="events" /><div v-if="!physicianRecord && interventions.length" class="intervention-record"><span class="intervention-icon"><Icon name="plus" :size="16" /></span><div><strong>干预记录</strong><p v-for="event in interventions" :key="event.action.action_id">{{ event.action.name }}<span>已写入患者历史</span></p></div></div>
                  <div class="clinical-note"><span class="note-label"><Icon name="file" :size="14" />病程记录</span><p>{{ note || '本时间节点暂无病程摘要。' }}</p></div>
                  <div class="detail-footer"><span>患者 {{ job.patient_id }}<span class="dot-separator">·</span>{{ job.request.backend === 'demo' ? '离线合成规则' : 'EHRWorld API' }}</span><button v-if="isRunning && !followLatest" @click="followLatest = true; selectedStep = Math.max(0, steps.length - 1)">跟随最新进度 <Icon name="arrow" :size="13" /></button><span v-else>{{ selectedStep + 1 }} / {{ totalSteps }}</span></div>
                </div>
              </section>
            </div>
          </div>
        </template>

        <section v-else-if="view === 'history'" class="card history-panel"><div class="panel-heading"><h2>最近的生成任务 <span class="count-badge">{{ history.length }}</span></h2><button class="button secondary" @click="refreshHistory().catch(() => error = '任务记录加载失败。')"><Icon name="refresh" :size="15" />刷新</button></div><div v-if="!history.length" class="empty-ehr"><Icon name="clock" :size="35" /><h3>还没有生成记录</h3><p>创建第一条患者轨迹后，记录将保存在这里。</p><button class="button primary" @click="view = 'workbench'">开始生成<Icon name="arrow" :size="16" /></button></div><button v-for="item in history" :key="item.id" class="history-row" @click="selectJob(item.id)"><span class="avatar"><Icon name="user" /></span><div class="history-patient"><strong>{{ item.request.patient.name }}</strong><small>{{ item.request.patient.primary_diagnosis }}</small></div><span class="history-mode">{{ item.request.backend === 'api' ? 'API 模型' : '离线演示' }}</span><span class="history-time">{{ formatTime(item.created_at, true) }}</span><span class="status-pill" :class="item.status">{{ statusLabels[item.status] }}</span><span class="history-count">{{ item.completed_steps }}/{{ item.total_steps }}</span><Icon name="chevron" :size="16" /></button></section>
        <footer class="page-footer"><span><Icon name="pulse" :size="14" />EHRWorld · Patient-centric world model</span><span>合成研究数据 · 新生儿指南适配 · 研究与流程验证</span></footer>
      </main>
    </div>
    <Transition name="toast"><div v-if="notice" class="toast" role="status"><Icon name="check" :size="18" />{{ notice }}</div></Transition>
  </div>
</template>
