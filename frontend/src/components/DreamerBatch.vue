<script setup>
import {computed,onMounted,onBeforeUnmount,reactive,ref,watch} from 'vue'
import ModelSelect from './ModelSelect.vue'
import Icon from './Icon.vue'
import {request,post,downloadDreamerBatch} from '../api'
import {statusLabels,terminalLabels,workflowProgress} from '../dreamer-utils'
const props=defineProps({models:Array,initialBatchId:String,defaultModel:String})
const emit=defineEmits(['open','selected','updated'])
const form=reactive({model:props.defaultModel||'Qwen3.8-27B',backend:'api',seed:42,max_hours:168,max_followups:null})
const preview=ref(null),batch=ref(null),recent=ref([]),busy=ref(false),error=ref('')
const running=computed(()=>batch.value?.status==='running')
let timer,controller,selection=0,disposed=false
function stop(){clearTimeout(timer);controller?.abort();selection++}
watch(form,()=>preview.value=null)
async function refresh(){const r=await request('/dreamer/batches');if(!disposed)recent.value=r.items}
async function poll(id,token){controller=new AbortController();try{const r=await request(`/dreamer/batches/${id}`,{signal:controller.signal});if(disposed||token!==selection)return;batch.value=r;error.value='';if(r.status==='running')timer=setTimeout(()=>poll(id,token),1200);else{refresh().catch(()=>{});emit('updated')}}catch(e){if(e.name!=='AbortError'&&!disposed&&token===selection){error.value='批次进度连接中断，正在重试。';timer=setTimeout(()=>poll(id,token),2500)}}}
async function open(id){stop();busy.value=true;error.value='';try{const r=await request(`/dreamer/batches/${id}`);if(disposed)return;batch.value=r;preview.value=null;emit('selected',id);if(r.status==='running')poll(id,selection)}catch(e){error.value=e.message}finally{busy.value=false}}
async function plan(){busy.value=true;error.value='';try{const r=await post('/dreamer/batches/preview',{...form});if(!disposed)preview.value=r}catch(e){error.value=e.message}finally{busy.value=false}}
async function generate(){if(!preview.value)return;busy.value=true;error.value='';try{const r=await post('/dreamer/batches',{...form,source_sha256:preview.value.source.source_sha256});if(disposed)return;stop();batch.value=r;preview.value=null;emit('selected',r.id);emit('updated');poll(r.id,selection);await refresh()}catch(e){error.value=e.message}finally{busy.value=false}}
async function download(){try{await downloadDreamerBatch(batch.value.id)}catch(e){error.value=e.message}}
function reset(){stop();batch.value=null;preview.value=null;emit('selected',null)}
onMounted(async()=>{try{await refresh();if(props.initialBatchId)await open(props.initialBatchId)}catch(e){error.value=e.message}})
onBeforeUnmount(()=>{disposed=true;stop()})
</script>
<template>
  <div class="ds-batch-workspace"><div v-if="error" class="ds-alert" role="alert">{{ error }}</div>
    <section v-if="!batch" class="ds-card ds-batch-config"><div class="ds-panel-head"><div><h2>生成10名患者的独立病程</h2><p>按初始信息采样，不提前指定出院诊断或治疗结果。</p></div><Icon name="layers"/></div><fieldset :disabled="busy"><div class="ds-batch-fields"><ModelSelect v-model="form.model" :models="models"/><label class="ds-field">生成模式<select v-model="form.backend"><option value="api">API模型</option><option value="demo">离线闭环演示</option></select></label><label class="ds-field">随机种子<input v-model.number="form.seed" type="number" min="0" max="2147483647"/></label><label class="ds-field">模拟时长上限（小时）<input v-model.number="form.max_hours" type="number" min="6" max="720"/></label></div><p class="ds-note">一次入院检查、治疗与针对性复查、治疗结果汇总；默认最多2轮复查，不把操作预算当成必须用完的检查次数。3名短预算（24–32操作）、5名中预算（48–64操作）、2名长预算（80–96操作）。预算结束会保留未完成病程，不强行出院。</p><button class="ds-button primary" @click="plan"><span v-if="busy" class="spinner"></span><Icon v-else name="layers" :size="17"/>预览10名患者</button></fieldset>
      <template v-if="preview"><div class="ds-table-scroll"><table class="ds-table"><thead><tr><th>患者</th><th>检查前表型</th><th>胎龄</th><th>操作预算</th></tr></thead><tbody><tr v-for="p in preview.patients" :key="p.index"><td>{{ p.case.label }}</td><td>{{ p.case.phenotype }}</td><td>{{ p.case.intake.gestational_weeks??'待核实' }}</td><td>{{ p.max_operations }}</td></tr></tbody></table></div><div class="ds-panel-actions"><span class="ds-muted">来自新工作簿；10个不同来源组。</span><button class="ds-button primary" :disabled="busy" @click="generate">开始10人模拟<Icon name="arrow" :size="16"/></button></div></template>
    </section>
    <section v-else class="ds-card ds-batch-results"><div class="ds-panel-head"><div><h2>批次患者病程 <span class="ds-status" :class="batch.status">{{ statusLabels[batch.status] }}</span></h2><p>{{ batch.request.model }} · {{ batch.request.backend==='demo'?'离线闭环演示':'API模型' }}</p></div><div class="ds-toolbar"><button class="ds-button" :disabled="batch.status!=='completed'" @click="download">导出整批JSON</button><button class="ds-button" :disabled="running" @click="reset">新建批次</button></div></div><div class="ds-batch-stat"><strong>{{ batch.completed_patients }} / 10 <small>任务完成</small></strong><span>{{ batch.completed_trajectories||0 }}名已生成治疗结果 · {{ batch.failed_patients }}名任务失败</span></div><div class="ds-patient-grid"><button v-for="p in batch.patients" :key="p.id" class="ds-patient-tile" @click="emit('open',p.id,batch.id)"><div><span class="ds-case-number">{{ String(p.batch_index).padStart(2,'0') }}</span><span class="ds-status" :class="p.status">{{ terminalLabels[p.terminal_status]||statusLabels[p.status] }}</span></div><h3>{{ p.case.label }}</h3><p>{{ p.case.phenotype }} · {{ p.case.intake.gestational_weeks==null?'孕周待核实':p.case.intake.gestational_weeks+'周' }}</p><div class="ds-progress"><i :style="{width:`${workflowProgress(p)}%`}"></i></div><div class="ds-tile-bottom"><span>{{ p.operations }} / {{ p.request.max_operations }}操作</span><span>查看病历 <Icon name="arrow" :size="14"/></span></div><p v-if="p.error" class="ds-error-text">{{ p.error.message }}</p></button></div></section>
    <section class="ds-card ds-history"><div class="ds-panel-head"><h2>最近批次</h2><button class="ds-button" @click="refresh().catch(e=>error=e.message)">刷新</button></div><button v-for="b in recent" :key="b.id" class="ds-history-row" @click="open(b.id)"><strong>{{ b.id.slice(0,8) }}</strong><span>{{ b.request.backend==='demo'?'离线演示':b.request.model }}</span><span>{{ statusLabels[b.status] }} · {{ b.completed_patients }}/10名</span><Icon name="chevron" :size="16"/></button><p v-if="!recent.length" class="ds-muted">还没有批次，先预览患者。</p></section>
  </div>
</template>
