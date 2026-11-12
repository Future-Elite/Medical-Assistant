<template>
  <div class="his-shell">
    <aside class="sidebar">
      <router-link to="/" class="brand"><span class="brand-mark"><FirstAidKit /></span><span>新生儿 HIS<small>NEONATAL RECORDS</small></span></router-link>
      <div class="nav-caption">病案工作台</div>
      <nav><router-link v-for="item in navigation" :key="item.path" :to="item.path" :class="{ active: active === item.path }"><component :is="item.icon" /><span>{{ item.label }}</span></router-link></nav>
      <div class="sidebar-note"><span class="status-dot"></span> 出院病案数据中心<p>保留原始小结<br>每条记录均可追溯来源</p></div>
    </aside>
    <section class="workspace">
      <header class="topbar">
        <span class="breadcrumb">新生儿科 <span>/</span> {{ navigation.find(n => n.path === active)?.label || '病案详情' }}</span>
        <div class="dataset-selector"><span>当前数据集</span><el-select v-model="selectedBatch" placeholder="尚未导入数据" :disabled="!batches.length" style="width: 290px" aria-label="当前数据集"><el-option v-for="b in batches" :key="b.id" :value="b.id" :label="`#${b.id} ${b.fileName}`" /></el-select></div>
      </header>
      <main class="page-content"><router-view /></main>
    </section>
  </div>
</template>
<script setup>
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { FirstAidKit, Files, DataAnalysis, DocumentChecked, UploadFilled } from '@element-plus/icons-vue'
import { batches, selectedBatch, refreshBatches } from './api/neonatal'
const route = useRoute()
const navigation = [{ path: '/', label: '新生儿病案', icon: Files }, { path: '/dashboard', label: '专科数据看板', icon: DataAnalysis }, { path: '/validation', label: '数据质量', icon: DocumentChecked }, { path: '/import', label: '数据导入', icon: UploadFilled }]
const active = computed(() => route.path.startsWith('/neonatal/') ? '/' : route.path)
onMounted(() => refreshBatches().catch(() => {}))
</script>
