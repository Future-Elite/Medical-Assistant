<script setup>
import {computed,onMounted,ref} from 'vue'
import {request} from '../api'
const data=ref(null),error=ref(''),stage=ref('transition')
const names={spo2:'SpO₂',sao2:'血气sO₂',respiratory_rate:'呼吸频率',heart_rate:'心率',temperature:'体温',ph:'血气pH',paco2:'PaCO₂',crp:'CRP',pct:'PCT',wbc:'白细胞',bilirubin:'总胆红素',weight_g:'体重',feeding:'喂养标签'}
const rows=computed(()=>Object.entries(data.value?.report.models[stage.value]||{}))
const decimal=value=>value==null?'—':Number(value).toFixed(3)
onMounted(async()=>{try{data.value=await request('/dreamer/learning')}catch(e){error.value=e.message}})
</script>
<template>
  <section class="ds-card ds-learning-panel">
    <div class="ds-panel-head"><div><h2>本地机器学习模型 · 训练与验证</h2><p>真实来源标签训练，按来源标识分组留出；检验结果不再由人工生理公式生成。</p></div></div>
    <p v-if="error" class="ds-error-text" role="alert">{{ error }}</p>
    <template v-if="data">
      <div class="ds-four-stats"><div><span>训练分组</span><strong>{{ data.report.group_counts.train }}</strong></div><div><span>验证分组</span><strong>{{ data.report.group_counts.validation }}</strong></div><div><span>独立测试分组</span><strong>{{ data.report.group_counts.test }}</strong></div><div><span>相邻时点记录</span><strong>{{ data.report.dataset_counts.transitions.toLocaleString() }}</strong></div></div>
      <p class="ds-note">分组交叉数：{{ data.report.group_overlap }}。仅将有明确日期先后的检验作为转移标签；未知治疗状态保留缺失。动作相关性尚不能解释为因果治疗效应。</p>
      <div class="ds-phase-tabs" role="tablist" aria-label="训练任务"><button role="tab" :aria-selected="stage==='transition'" :class="{active:stage==='transition'}" @click="stage='transition'">日级状态转移</button><button role="tab" :aria-selected="stage==='initial'" :class="{active:stage==='initial'}" @click="stage='initial'">初始检查分布</button></div>
      <div class="ds-table-scroll"><table class="ds-table"><thead><tr><th>指标</th><th>验证集选定模型</th><th>训练 / 测试记录</th><th>测试MAE</th><th>基线MAE</th></tr></thead><tbody><tr v-for="[metric,result] in rows" :key="metric"><td>{{ names[metric]||metric }}</td><td>{{ result.status==='trained'?result.selected_algorithm:result.status==='insufficient_class_balance'?'正负标签不足':'重复观测不足' }}</td><td>{{ result.counts?.train||0 }} / {{ result.counts?.test||0 }}</td><td>{{ decimal(result.selected_test?.mae) }}</td><td>{{ decimal(result.test_candidates?.[stage==='transition'?'persistence':'training_median']?.mae) }}</td></tr></tbody></table></div>
      <p class="ds-muted">MAE沿用各指标原单位；基线分别为保持上一观测、训练集标签中位数。模型选择使用验证组，测试组不参与拟合、选择或校准。这里是条件一步预测误差，不等于长病程生成准确率。</p>
      <h3>检查类别模型</h3><div v-for="(result,target) in data.report.models.images" :key="target" class="ds-condition"><strong>{{ target==='chest_xray'?'胸片文本表现类别':'肺部超声' }}</strong><span>{{ result.status==='trained'?'已训练 · 测试macro-F1 '+decimal(result.test_macro_f1):result.status==='trained_not_qualified'?'验证未达门槛，禁止生成结果':'样本不足，禁止自动生成结果' }}</span></div>
      <p class="ds-note">时间分辨率为{{ data.capabilities.time_resolution_hours }}小时。血氧、呼吸、心率等当前仅支持初始分布，不把保持初值显示成已经学会治疗后变化；喂养、超声和培养等标签不足的项目不会回退到手写映射。</p>
      <details><summary>模型来源与训练边界</summary><p>模型版本 {{ data.report.version }} · ID {{ data.capabilities.model_id }}</p><p>训练时间 {{ data.report.trained_at }} · scikit-learn {{ data.report.sklearn_version }}</p><p v-for="note in data.report.limitations" :key="note">{{ note }}</p><p>数据抽取与训练均在本地执行，不调用外部大模型生成监督标签。</p><pre>python -B scripts/train_dreamer.py
python -B scripts/evaluate_dreamer_rollout.py</pre></details>
    </template>
    <p v-else-if="!error">正在读取训练报告…</p>
  </section>
</template>
