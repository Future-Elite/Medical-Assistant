<template>
  <el-card shadow="never" class="entry-card">
    <div class="page-head">
      <div>
        <h2 class="page-title">诊疗录入</h2>
        <p class="page-tip">患者 ID 为必填项；诊断 / 处方 / 检验还需填写所属就诊 ID。</p>
      </div>
      <el-button v-if="lastPatientId" type="success" plain @click="goTimeline">
        查看时间轴（患者 {{ lastPatientId }}）
      </el-button>
    </div>

    <el-tabs v-model="activeTab">
      <!-- 1. 挂号登记 -->
      <el-tab-pane label="挂号登记" name="visit">
        <el-form
          ref="visitFormRef"
          :model="visitForm"
          :rules="visitRules"
          label-width="96px"
          class="entry-form"
        >
          <el-row :gutter="16">
            <el-col :xs="24" :md="12">
              <el-form-item label="患者 ID" prop="patientId">
                <el-input-number
                  v-model="visitForm.patientId"
                  class="full"
                  :min="1"
                  :precision="0"
                  :controls="false"
                  placeholder="必填"
                />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="就诊号" prop="visitNo">
                <el-input v-model="visitForm.visitNo" placeholder="如 GH20240101001" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="科室" prop="department">
                <el-input v-model="visitForm.department" placeholder="如 内科" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="医生" prop="doctor">
                <el-input v-model="visitForm.doctor" placeholder="如 张三" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="就诊时间" prop="visitTime">
                <el-date-picker
                  v-model="visitForm.visitTime"
                  class="full"
                  type="datetime"
                  value-format="YYYY-MM-DDTHH:mm:ss"
                  placeholder="选择就诊时间"
                />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="状态" prop="status">
                <el-select v-model="visitForm.status" class="full">
                  <el-option label="待就诊" :value="0" />
                  <el-option label="已完成" :value="1" />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="24">
              <el-form-item label="备注" prop="remark">
                <el-input
                  v-model="visitForm.remark"
                  type="textarea"
                  :rows="2"
                  maxlength="200"
                  show-word-limit
                  placeholder="可选"
                />
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item>
            <el-button type="primary" :loading="submitting" @click="submitVisit">
              提交挂号
            </el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- 2. 诊断录入 -->
      <el-tab-pane label="诊断录入" name="diagnosis">
        <el-form
          ref="diagnosisFormRef"
          :model="diagnosisForm"
          :rules="diagnosisRules"
          label-width="96px"
          class="entry-form"
        >
          <el-row :gutter="16">
            <el-col :xs="24" :md="12">
              <el-form-item label="患者 ID" prop="patientId">
                <el-input-number
                  v-model="diagnosisForm.patientId"
                  class="full"
                  :min="1"
                  :precision="0"
                  :controls="false"
                  placeholder="必填"
                />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="就诊 ID" prop="visitId">
                <el-input-number
                  v-model="diagnosisForm.visitId"
                  class="full"
                  :min="1"
                  :precision="0"
                  :controls="false"
                  placeholder="必填"
                />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="ICD 编码" prop="icdCode">
                <el-input v-model="diagnosisForm.icdCode" placeholder="如 J06.9" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="诊断名称" prop="diagnosisName">
                <el-input v-model="diagnosisForm.diagnosisName" placeholder="如 上呼吸道感染" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="医生" prop="doctor">
                <el-input v-model="diagnosisForm.doctor" placeholder="如 张三" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="诊断时间" prop="diagnosedAt">
                <el-date-picker
                  v-model="diagnosisForm.diagnosedAt"
                  class="full"
                  type="datetime"
                  value-format="YYYY-MM-DDTHH:mm:ss"
                  placeholder="选择诊断时间"
                />
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item>
            <el-button type="primary" :loading="submitting" @click="submitDiagnosis">
              提交诊断
            </el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- 3. 处方开具 -->
      <el-tab-pane label="处方开具" name="prescription">
        <el-form
          ref="prescriptionFormRef"
          :model="prescriptionForm"
          :rules="prescriptionRules"
          label-width="96px"
          class="entry-form"
        >
          <el-row :gutter="16">
            <el-col :xs="24" :md="12">
              <el-form-item label="患者 ID" prop="patientId">
                <el-input-number
                  v-model="prescriptionForm.patientId"
                  class="full"
                  :min="1"
                  :precision="0"
                  :controls="false"
                  placeholder="必填"
                />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="就诊 ID" prop="visitId">
                <el-input-number
                  v-model="prescriptionForm.visitId"
                  class="full"
                  :min="1"
                  :precision="0"
                  :controls="false"
                  placeholder="必填"
                />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="医生" prop="doctor">
                <el-input v-model="prescriptionForm.doctor" placeholder="如 张三" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="开方时间" prop="prescribedAt">
                <el-date-picker
                  v-model="prescriptionForm.prescribedAt"
                  class="full"
                  type="datetime"
                  value-format="YYYY-MM-DDTHH:mm:ss"
                  placeholder="选择开方时间"
                />
              </el-form-item>
            </el-col>
          </el-row>

          <el-form-item label="药品明细" required>
            <el-table :data="prescriptionForm.items" border size="small" class="item-table">
              <el-table-column label="药名" min-width="200">
                <template #default="{ row }">
                  <el-select
                    v-model="row.drugId"
                    class="full"
                    filterable
                    placeholder="选择药品"
                    @change="onDrugChange(row)"
                  >
                    <el-option
                      v-for="drug in DRUGS"
                      :key="drug.id"
                      :label="`${drug.name}（${drug.unit} ¥${drug.price.toFixed(2)}）`"
                      :value="drug.id"
                    />
                  </el-select>
                </template>
              </el-table-column>
              <el-table-column label="用量" width="120">
                <template #default="{ row }">
                  <el-input v-model="row.dosage" placeholder="如 1粒" />
                </template>
              </el-table-column>
              <el-table-column label="频次" width="120">
                <template #default="{ row }">
                  <el-input v-model="row.frequency" placeholder="如 每日3次" />
                </template>
              </el-table-column>
              <el-table-column label="天数" width="100">
                <template #default="{ row }">
                  <el-input-number
                    v-model="row.days"
                    class="full"
                    :min="1"
                    :precision="0"
                    :controls="false"
                  />
                </template>
              </el-table-column>
              <el-table-column label="数量" width="100">
                <template #default="{ row }">
                  <el-input-number
                    v-model="row.quantity"
                    class="full"
                    :min="0.01"
                    :precision="2"
                    :controls="false"
                  />
                </template>
              </el-table-column>
              <el-table-column label="单位" width="90">
                <template #default="{ row }">
                  <el-input v-model="row.unit" />
                </template>
              </el-table-column>
              <el-table-column label="操作" width="80" align="center">
                <template #default="{ $index }">
                  <el-button link type="danger" @click="removeItem($index)">删除</el-button>
                </template>
              </el-table-column>
            </el-table>
            <el-button class="add-item" @click="addItem">+ 添加药品</el-button>
            <div class="item-tip">金额由后端按药品单价 × 数量计算，请务必选择药品。</div>
          </el-form-item>

          <el-form-item>
            <el-button type="primary" :loading="submitting" @click="submitPrescription">
              提交处方
            </el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- 4. 检验录入 -->
      <el-tab-pane label="检验录入" name="lab">
        <el-form
          ref="labFormRef"
          :model="labForm"
          :rules="labRules"
          label-width="96px"
          class="entry-form"
        >
          <el-row :gutter="16">
            <el-col :xs="24" :md="12">
              <el-form-item label="患者 ID" prop="patientId">
                <el-input-number
                  v-model="labForm.patientId"
                  class="full"
                  :min="1"
                  :precision="0"
                  :controls="false"
                  placeholder="必填"
                />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="就诊 ID" prop="visitId">
                <el-input-number
                  v-model="labForm.visitId"
                  class="full"
                  :min="1"
                  :precision="0"
                  :controls="false"
                  placeholder="必填"
                />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="项目编码" prop="itemCode">
                <el-input v-model="labForm.itemCode" placeholder="如 WBC" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="项目名称" prop="itemName">
                <el-input v-model="labForm.itemName" placeholder="如 白细胞计数" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="结果值" prop="resultValue">
                <el-input v-model="labForm.resultValue" placeholder="如 12.5" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="单位" prop="unit">
                <el-input v-model="labForm.unit" placeholder="如 10^9/L" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="参考范围" prop="referenceRange">
                <el-input v-model="labForm.referenceRange" placeholder="如 4.0-10.0" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="检验时间" prop="testedAt">
                <el-date-picker
                  v-model="labForm.testedAt"
                  class="full"
                  type="datetime"
                  value-format="YYYY-MM-DDTHH:mm:ss"
                  placeholder="选择检验时间"
                />
              </el-form-item>
            </el-col>
            <el-col :span="24">
              <el-form-item label="结果判定" prop="isAbnormal">
                <el-switch
                  v-model="labForm.isAbnormal"
                  :active-value="1"
                  :inactive-value="0"
                  active-text="异常"
                  inactive-text="正常"
                />
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item>
            <el-button type="primary" :loading="submitting" @click="submitLab">
              提交检验
            </el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>
    </el-tabs>
  </el-card>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { createVisit, createDiagnosis, createPrescription, createLabTest } from '../api/business'

const router = useRouter()

const activeTab = ref('visit')
const submitting = ref(false)
// 最近一次成功提交的患者 ID，用于「查看时间轴」跳转
const lastPatientId = ref(null)

// 药品字典：后端未提供 /api/drugs 接口，硬编码 seed 的 12 条药品（id 即插入顺序）
// 明细必须携带合法 drugId —— 后端据此取单价计算 totalAmount
const DRUGS = [
  { id: 1, name: '阿莫西林胶囊', unit: '盒', price: 18.5 },
  { id: 2, name: '感冒灵颗粒', unit: '盒', price: 12.0 },
  { id: 3, name: '布洛芬缓释胶囊', unit: '盒', price: 15.8 },
  { id: 4, name: '硝苯地平控释片', unit: '盒', price: 28.0 },
  { id: 5, name: '二甲双胍片', unit: '盒', price: 9.6 },
  { id: 6, name: '奥美拉唑肠溶胶囊', unit: '盒', price: 22.0 },
  { id: 7, name: '蒙脱石散', unit: '盒', price: 16.5 },
  { id: 8, name: '左氧氟沙星片', unit: '盒', price: 26.0 },
  { id: 9, name: '阿司匹林肠溶片', unit: '盒', price: 13.2 },
  { id: 10, name: '苯磺酸氨氯地平片', unit: '盒', price: 19.8 },
  { id: 11, name: '格列美脲片', unit: '盒', price: 24.5 },
  { id: 12, name: '复方甘草口服液', unit: '瓶', price: 8.9 }
]

// 本地时间 -> ISO-8601（T 分隔），后端 LocalDateTime 反序列化不接受空格分隔
const nowIso = () => {
  const d = new Date()
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

const newVisitForm = () => ({
  patientId: null,
  visitNo: '',
  department: '',
  doctor: '',
  visitTime: nowIso(),
  status: 0,
  remark: ''
})
const newDiagnosisForm = () => ({
  patientId: null,
  visitId: null,
  icdCode: '',
  diagnosisName: '',
  doctor: '',
  diagnosedAt: nowIso()
})
const newItem = () => ({
  drugId: null,
  drugName: '',
  dosage: '',
  frequency: '',
  days: 3,
  quantity: 1,
  unit: ''
})
const newPrescriptionForm = () => ({
  patientId: null,
  visitId: null,
  doctor: '',
  prescribedAt: nowIso(),
  items: [newItem()]
})
const newLabForm = () => ({
  patientId: null,
  visitId: null,
  itemCode: '',
  itemName: '',
  resultValue: '',
  unit: '',
  referenceRange: '',
  isAbnormal: 0,
  testedAt: nowIso()
})

const visitForm = ref(newVisitForm())
const diagnosisForm = ref(newDiagnosisForm())
const prescriptionForm = ref(newPrescriptionForm())
const labForm = ref(newLabForm())

const visitFormRef = ref()
const diagnosisFormRef = ref()
const prescriptionFormRef = ref()
const labFormRef = ref()

const required = (message) => ({ required: true, message, trigger: ['blur', 'change'] })

const visitRules = {
  patientId: [required('请输入患者 ID')],
  visitNo: [required('请输入就诊号')],
  visitTime: [required('请选择就诊时间')]
}
const diagnosisRules = {
  patientId: [required('请输入患者 ID')],
  visitId: [required('请输入就诊 ID')],
  diagnosisName: [required('请输入诊断名称')],
  diagnosedAt: [required('请选择诊断时间')]
}
const prescriptionRules = {
  patientId: [required('请输入患者 ID')],
  visitId: [required('请输入就诊 ID')],
  prescribedAt: [required('请选择开方时间')]
}
const labRules = {
  patientId: [required('请输入患者 ID')],
  visitId: [required('请输入就诊 ID')],
  itemName: [required('请输入检验项目名称')],
  testedAt: [required('请选择检验时间')]
}

const validateForm = async (formRef) => {
  if (!formRef.value) return false
  try {
    await formRef.value.validate()
    return true
  } catch {
    return false
  }
}

// 统一提交：失败时 request 拦截器已 ElMessage.error，这里只需提前返回
const submit = async (fn, successMessage, patientId) => {
  submitting.value = true
  try {
    await fn()
  } catch {
    return false
  } finally {
    submitting.value = false
  }
  ElMessage.success(successMessage)
  lastPatientId.value = patientId
  return true
}

const submitVisit = async () => {
  if (!(await validateForm(visitFormRef))) return
  const payload = { ...visitForm.value }
  if (await submit(() => createVisit(payload), '挂号登记成功', payload.patientId)) {
    visitForm.value = newVisitForm()
  }
}

const submitDiagnosis = async () => {
  if (!(await validateForm(diagnosisFormRef))) return
  const payload = { ...diagnosisForm.value }
  if (await submit(() => createDiagnosis(payload), '诊断录入成功', payload.patientId)) {
    diagnosisForm.value = newDiagnosisForm()
  }
}

const addItem = () => prescriptionForm.value.items.push(newItem())
const removeItem = (index) => prescriptionForm.value.items.splice(index, 1)

// 选中药品后回填 drugName / unit（展示用副本）
const onDrugChange = (row) => {
  const drug = DRUGS.find((d) => d.id === row.drugId)
  if (!drug) return
  row.drugName = drug.name
  row.unit = drug.unit
}

const submitPrescription = async () => {
  if (!(await validateForm(prescriptionFormRef))) return
  const rows = prescriptionForm.value.items
  if (!rows.length) {
    ElMessage.warning('请至少添加一条药品明细')
    return
  }
  if (rows.some((i) => !i.drugId)) {
    ElMessage.warning('每条明细都必须选择药品')
    return
  }
  const payload = {
    patientId: prescriptionForm.value.patientId,
    visitId: prescriptionForm.value.visitId,
    doctor: prescriptionForm.value.doctor,
    prescribedAt: prescriptionForm.value.prescribedAt,
    items: rows.map((i) => ({
      drugId: i.drugId,
      drugName: i.drugName,
      dosage: i.dosage,
      frequency: i.frequency,
      days: i.days,
      // quantity 为 DECIMAL，显式转数字
      quantity: Number(i.quantity),
      unit: i.unit
    }))
  }
  if (await submit(() => createPrescription(payload), '处方开具成功', payload.patientId)) {
    prescriptionForm.value = newPrescriptionForm()
  }
}

const submitLab = async () => {
  if (!(await validateForm(labFormRef))) return
  const payload = { ...labForm.value }
  if (await submit(() => createLabTest(payload), '检验录入成功', payload.patientId)) {
    labForm.value = newLabForm()
  }
}

const goTimeline = () => router.push('/patient/' + lastPatientId.value)
</script>

<style scoped>
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.page-title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: #303133;
}

.page-tip {
  margin: 4px 0 0;
  font-size: 13px;
  color: #909399;
}

.entry-form {
  max-width: 900px;
}

.full {
  width: 100%;
}

.item-table {
  width: 100%;
}

.add-item {
  margin-top: 8px;
}

.item-tip {
  margin-top: 4px;
  font-size: 12px;
  color: #909399;
}
</style>
