<template>
  <el-card shadow="never">
    <div class="toolbar">
      <el-input
        v-model="keyword"
        class="keyword"
        placeholder="姓名 / 身份证号 / 电话"
        clearable
        @keyup.enter="handleSearch"
        @clear="handleSearch"
      />
      <el-button type="primary" @click="handleSearch">搜索</el-button>
      <el-button @click="handleReset">重置</el-button>
    </div>

    <el-table
      v-loading="loading"
      :data="records"
      border
      stripe
      class="patient-table"
      style="width: 100%"
      @row-click="handleRowClick"
    >
      <el-table-column prop="name" label="姓名" min-width="100" />
      <el-table-column label="性别" width="80">
        <template #default="{ row }">{{ genderText(row.gender) }}</template>
      </el-table-column>
      <el-table-column label="年龄" width="80">
        <template #default="{ row }">{{ ageText(row.birthDate) }}</template>
      </el-table-column>
      <el-table-column prop="idCard" label="身份证号" min-width="180" />
      <el-table-column prop="phone" label="电话" min-width="130" />
      <el-table-column prop="address" label="地址" min-width="200" show-overflow-tooltip />
    </el-table>

    <el-pagination
      class="pager"
      background
      layout="total, sizes, prev, pager, next, jumper"
      :current-page="page"
      :page-size="size"
      :page-sizes="[10, 20, 50]"
      :total="total"
      @current-change="handlePageChange"
      @size-change="handleSizeChange"
    />
  </el-card>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { listPatients } from '../api/patient'

const router = useRouter()

const records = ref([])
const total = ref(0)
const page = ref(1)
const size = ref(10)
const keyword = ref('')
const loading = ref(false)

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

const fetchList = async () => {
  loading.value = true
  try {
    const res = await listPatients({
      page: page.value,
      size: size.value,
      keyword: keyword.value || undefined
    })
    records.value = res.data?.records || []
    total.value = res.data?.total || 0
  } catch (e) {
    records.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

const handleSearch = () => {
  page.value = 1
  fetchList()
}

const handleReset = () => {
  keyword.value = ''
  page.value = 1
  fetchList()
}

const handlePageChange = (current) => {
  page.value = current
  fetchList()
}

const handleSizeChange = (pageSize) => {
  size.value = pageSize
  page.value = 1
  fetchList()
}

const handleRowClick = (row) => {
  router.push('/patient/' + row.id)
}

onMounted(fetchList)
</script>

<style scoped>
.toolbar {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
}

.keyword {
  width: 260px;
}

.patient-table :deep(.el-table__row) {
  cursor: pointer;
}

.pager {
  margin-top: 16px;
  justify-content: flex-end;
}
</style>
