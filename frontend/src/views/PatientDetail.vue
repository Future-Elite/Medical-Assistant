<template>
  <div class="patient-detail">
    <div class="page-head">
      <el-button link type="primary" @click="goBack">&lt; 返回列表</el-button>
      <h2 class="page-title">{{ patient ? patient.name : '患者详情' }}</h2>
    </div>

    <el-alert
      v-if="error"
      class="error-alert"
      type="error"
      show-icon
      :closable="false"
      :title="error"
    />

    <el-row :gutter="16">
      <!-- 左：基本信息 -->
      <el-col :xs="24" :md="8" :lg="7">
        <el-card v-loading="loading" shadow="never" class="info-card">
          <template #header>
            <span class="card-title">基本信息</span>
          </template>
          <el-descriptions v-if="patient" :column="1" border size="small">
            <el-descriptions-item label="姓名">{{ patient.name || '-' }}</el-descriptions-item>
            <el-descriptions-item label="性别">{{ genderText(patient.gender) }}</el-descriptions-item>
            <el-descriptions-item label="年龄">{{ ageText(patient.birthDate) }}</el-descriptions-item>
            <el-descriptions-item label="身份证">{{ patient.idCard || '-' }}</el-descriptions-item>
            <el-descriptions-item label="电话">{{ patient.phone || '-' }}</el-descriptions-item>
            <el-descriptions-item label="地址">{{ patient.address || '-' }}</el-descriptions-item>
            <el-descriptions-item label="建档时间">
              {{ formatDateTime(patient.createdAt) || '-' }}
            </el-descriptions-item>
          </el-descriptions>
          <el-empty v-else-if="!loading" description="未找到该患者" />
        </el-card>
      </el-col>

      <!-- 右：诊疗时间轴 -->
      <el-col :xs="24" :md="16" :lg="17">
        <el-card v-loading="loading" shadow="never" class="timeline-card">
          <template #header>
            <span class="card-title">诊疗时间轴</span>
            <span v-if="events.length" class="event-count">共 {{ events.length }} 条记录</span>
          </template>
          <Timeline v-if="events.length" :events="events" />
          <el-empty v-else-if="!loading" description="暂无诊疗记录" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getPatient, getTimeline } from '../api/patient'
import Timeline from '../components/Timeline.vue'

const route = useRoute()
const router = useRouter()

const patient = ref(null)
const events = ref([])
const loading = ref(false)
const error = ref('')

const genderText = (gender) => (gender === 0 ? '男' : gender === 1 ? '女' : '-')

const ageText = (birthDate) => {
  if (!birthDate) return '-'
  // 用 / 分隔避免 UTC 解析导致的日期偏移
  const birth = new Date(String(birthDate).replace(/-/g, '/'))
  if (Number.isNaN(birth.getTime())) return '-'
  const today = new Date()
  let age = today.getFullYear() - birth.getFullYear()
  const monthDiff = today.getMonth() - birth.getMonth()
  if (monthDiff < 0 || (monthDiff === 0 && today.getDate() < birth.getDate())) age -= 1
  return age >= 0 ? String(age) : '-'
}

// "2024-01-01T09:00:00" -> "2024-01-01 09:00:00"
const formatDateTime = (t) => (t ? String(t).replace('T', ' ').slice(0, 19) : '')

const goBack = () => router.push('/')

const fetchDetail = async () => {
  loading.value = true
  error.value = ''
  const id = route.params.id
  // 并发请求，单个失败不影响另一个（拦截器已统一提示错误信息）
  const [patientRes, timelineRes] = await Promise.all([
    getPatient(id).catch(() => null),
    getTimeline(id).catch(() => null)
  ])
  patient.value = patientRes?.data || null
  events.value = timelineRes?.data?.events || []
  if (!patientRes) error.value = '患者信息加载失败'
  else if (!timelineRes) error.value = '诊疗时间轴加载失败'
  loading.value = false
}

onMounted(fetchDetail)
</script>

<style scoped>
.page-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.page-title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: #303133;
}

.error-alert {
  margin-bottom: 12px;
}

.card-title {
  font-weight: 600;
  color: #303133;
}

.event-count {
  margin-left: 12px;
  font-size: 13px;
  color: #909399;
}

.info-card,
.timeline-card {
  min-height: 200px;
}
</style>
