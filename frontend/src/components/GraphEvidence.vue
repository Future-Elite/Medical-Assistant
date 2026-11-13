<script setup>
import {computed,ref,watch} from 'vue'
const props=defineProps({context:{type:Object,default:null},compact:Boolean})
const selected=ref('')
const recommendations=computed(()=>props.context?.recommendations||[])
watch(recommendations,rows=>{if(!rows.some(r=>r.id===selected.value))selected.value=rows[0]?.id||''},{immediate:true})
const rule=computed(()=>recommendations.value.find(r=>r.id===selected.value))
const subgraph=computed(()=>{
 const graph=props.context?.graph||{nodes:[],edges:[]},rid='recommendation:'+selected.value
 const edges=graph.edges.filter(e=>e.source===rid||e.target===rid)
 const ids=new Set(edges.flatMap(e=>[e.source,e.target]));ids.add(rid)
 const columns=[[],[],[],[]]
 for(const n of graph.nodes.filter(n=>ids.has(n.id)&&n.type!=='population'))columns[n.type==='concept'?0:n.type==='recommendation'?1:n.type==='source'?3:2].push(n)
 const height=Math.max(170,...columns.map(c=>c.length*43+30)),nodes=[]
 columns.forEach((col,x)=>col.forEach((n,y)=>nodes.push({...n,x:8+x*197,y:18+(height-40-col.length*43)/2+y*43})))
 const byId=Object.fromEntries(nodes.map(n=>[n.id,n]))
 return {height,nodes,edges:edges.filter(e=>byId[e.source]&&byId[e.target]).map(e=>({...e,a:byId[e.source],b:byId[e.target]}))}
})
const label=n=>n.type==='source'?n.id.replace('source:',''):n.label
const relation={ADDRESSED_BY:'问题对应推荐',RECOMMENDS:'推荐动作',CONSTRAINS:'限制与条件',RECOMMENDS_OR_CONSTRAINS:'推荐或约束动作',MONITOR_WITH:'监测项目',SUPPORTED_BY:'来源支持',APPLIES_TO:'适用人群'}
</script>
<template>
 <div v-if="context" class="kg-evidence">
  <div class="wm-tags"><span v-for="term in context.normalized_terms?.slice(0,compact?6:100)" :key="term.id" class="wm-tag">{{ term.label }}</span></div>
  <label class="wm-inline">证据路径<select v-model="selected"><option v-for="r in recommendations" :key="r.id" :value="r.id">{{ r.id }}</option></select></label>
  <div v-if="rule" class="kg-rule"><p>{{ rule.summary }}</p><small>{{ rule.population_match==='conditional'?'人群条件待核实':'人群条件匹配；仍须核实临床指征' }}</small></div>
  <div v-if="compact&&rule" class="kg-compact-path"><div>推荐：<strong>{{ rule.id }}</strong></div><div v-if="rule.actions?.length">推荐动作：{{ rule.actions.map(k=>context.graph.nodes.find(n=>n.id==='action:'+k)?.label||k).join('、') }}</div><div v-if="rule.constrained_actions?.length">约束动作：{{ rule.constrained_actions.join('、') }}</div><div v-if="rule.monitoring_panels?.length">监测：{{ rule.monitoring_panels.map(k=>context.graph.nodes.find(n=>n.id==='exam:'+k)?.label||k).join('、') }}</div></div>
  <div class="kg-svg-wrap" v-else-if="subgraph.nodes.length"><svg :viewBox="`0 0 795 ${subgraph.height}`" role="img" :aria-label="`${selected}的医学图谱证据路径`">
   <path v-for="(e,i) in subgraph.edges" :key="i" :d="`M${e.a.x+172},${e.a.y+15} C${e.a.x+190},${e.a.y+15} ${e.b.x-18},${e.b.y+15} ${e.b.x},${e.b.y+15}`" fill="none" stroke="#c6d5e4" stroke-width="1.3"><title>{{ relation[e.relation]||e.relation }}</title></path>
   <g v-for="n in subgraph.nodes" :key="n.id" :class="'kg-'+n.type"><rect :x="n.x" :y="n.y" width="172" height="30" rx="5"/><text :x="n.x+8" :y="n.y+19">{{ label(n).length>20?label(n).slice(0,19)+'…':label(n) }}</text><title>{{ n.label }}</title></g>
  </svg></div>
  <div v-if="rule" class="kg-sources"><a v-for="id in rule.source_ids" :key="id" :href="context.sources[id]?.url" target="_blank" rel="noreferrer">{{ context.sources[id]?.title||id }} ↗</a></div>
  <div v-if="context.available_monitoring_panels?.length" class="wm-tags"><span v-for="panel in context.available_monitoring_panels" :key="panel.id" class="wm-tag">{{ panel.label }} · 本例{{ panel.metric_count }}项</span></div>
  <details v-if="context.excluded_recommendations?.length" class="wm-details"><summary>已排除 {{ context.excluded_recommendations.length }} 条人群不适用推荐</summary><span v-for="r in context.excluded_recommendations" :key="r.id" class="wm-tag">{{ r.id }}</span></details>
 </div>
</template>
