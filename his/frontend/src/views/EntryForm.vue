<template>
  <el-card shadow="never" class="entry-card">
    <div class="page-head">
      <div>
        <h2 class="page-title">诊疗录入</h2>
        <p class="page-tip">挂号登记、住院登记、收费、发药自由填写；诊断 / 处方 / 检验需先选医生，再选本科室的患者与就诊。</p>
      </div>
      <el-button v-if="lastPatientId" type="success" plain @click="goTimeline">
        查看时间轴（患者 {{ lastPatientId }}）
      </el-button>
    </div>

    <!-- 医生身份（轻量登录） -->
    <div class="doctor-bar">
      <span class="bar-label">当前医生</span>
      <el-select
        v-model="currentDoctor"
        filterable
        placeholder="请先选择医生（登录）"
        class="doctor-select"
        @change="onDoctorChange"
      >
        <el-option-group v-for="g in groupedDoctors" :key="g.department" :label="g.department">
          <el-option v-for="d in g.doctors" :key="d.id" :label="d.name" :value="d" />
        </el-option-group>
      </el-select>
      <span v-if="currentDoctor" class="doctor-dept">科室：{{ currentDoctor.department }}</span>
    </div>

    <!-- 当前接诊：患者 + 就诊 + 上下文 -->
    <div v-if="currentDoctor" class="reception-bar">
      <el-select
        v-model="currentPatient"
        filterable
        remote
        clearable
        :remote-method="searchPatients"
        :loading="patientLoading"
        value-key="id"
        placeholder="搜索本科室患者（姓名/身份证/电话）"
        class="reception-select"
        @change="onPatientChange"
      >
        <el-option v-for="p in patientOptions" :key="p.id" :label="patientLabel(p)" :value="p" />
      </el-select>

      <el-select
        v-model="currentVisit"
        clearable
        :disabled="!currentPatient"
        placeholder="选择就诊"
        class="reception-select"
      >
        <el-option v-for="v in visitOptions" :key="v.id" :label="visitLabel(v)" :value="v" />
      </el-select>

      <div v-if="currentPatient" class="ctx-line">
        患者 {{ currentPatient.name }}
        <span class="muted">（{{ genderText(currentPatient.gender) }}/{{ ageText(currentPatient.birthDate) }}）</span>
        <template v-if="currentVisit">
          ｜ 就诊 {{ currentVisit.visitNo }} · {{ currentVisit.department }} · {{ formatDateTime(currentVisit.visitTime) }}
        </template>
      </div>
    </div>

    <el-tabs v-model="activeTab">
      <!-- 1. 挂号登记（不变） -->
      <el-tab-pane label="挂号登记" name="visit">
        <el-form ref="visitFormRef" :model="visitForm" :rules="visitRules" label-width="96px" class="entry-form">
          <el-row :gutter="16">
            <el-col :xs="24" :md="12">
              <el-form-item label="患者 ID" prop="patientId">
                <el-input-number v-model="visitForm.patientId" class="full" :min="1" :precision="0" :controls="false" placeholder="必填" />
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
                <el-date-picker v-model="visitForm.visitTime" class="full" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" placeholder="选择就诊时间" />
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
                <el-input v-model="visitForm.remark" type="textarea" :rows="2" maxlength="200" show-word-limit placeholder="可选" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item>
            <el-button type="primary" :loading="submitting" @click="submitVisit">提交挂号</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- 2. 诊断录入 -->
      <el-tab-pane label="诊断录入" name="diagnosis">
        <el-alert v-if="!currentDoctor" class="tab-guard" type="info" show-icon :closable="false" title="请先选择医生（登录）" />
        <el-alert v-else-if="!currentPatient || !currentVisit" class="tab-guard" type="info" show-icon :closable="false" title="请先选择患者和就诊" />
        <el-form v-else ref="diagnosisFormRef" :model="diagnosisForm" :rules="diagnosisRules" label-width="96px" class="entry-form">
          <el-row :gutter="16">
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
              <el-form-item label="诊断时间" prop="diagnosedAt">
                <el-date-picker v-model="diagnosisForm.diagnosedAt" class="full" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" placeholder="选择诊断时间" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item>
            <el-button type="primary" :loading="submitting" @click="submitDiagnosis">提交诊断</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- 3. 处方开具 -->
      <el-tab-pane label="处方开具" name="prescription">
        <el-alert v-if="!currentDoctor" class="tab-guard" type="info" show-icon :closable="false" title="请先选择医生（登录）" />
        <el-alert v-else-if="!currentPatient || !currentVisit" class="tab-guard" type="info" show-icon :closable="false" title="请先选择患者和就诊" />
        <el-form v-else ref="prescriptionFormRef" :model="prescriptionForm" :rules="prescriptionRules" label-width="96px" class="entry-form">
          <el-row :gutter="16">
            <el-col :xs="24" :md="12">
              <el-form-item label="开方时间" prop="prescribedAt">
                <el-date-picker v-model="prescriptionForm.prescribedAt" class="full" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" placeholder="选择开方时间" />
              </el-form-item>
            </el-col>
          </el-row>

          <el-form-item label="药品明细" required>
            <el-table :data="prescriptionForm.items" border size="small" class="item-table">
              <el-table-column label="药名" min-width="200">
                <template #default="{ row }">
                  <el-select v-model="row.drugId" class="full" filterable placeholder="选择药品" @change="onDrugChange(row)">
                    <el-option v-for="drug in DRUGS" :key="drug.id" :label="`${drug.name}（${drug.unit} ¥${drug.price.toFixed(2)}）`" :value="drug.id" />
                  </el-select>
                </template>
              </el-table-column>
              <el-table-column label="用量" width="120">
                <template #default="{ row }"><el-input v-model="row.dosage" placeholder="如 1粒" /></template>
              </el-table-column>
              <el-table-column label="频次" width="120">
                <template #default="{ row }"><el-input v-model="row.frequency" placeholder="如 每日3次" /></template>
              </el-table-column>
              <el-table-column label="天数" width="100">
                <template #default="{ row }"><el-input-number v-model="row.days" class="full" :min="1" :precision="0" :controls="false" /></template>
              </el-table-column>
              <el-table-column label="数量" width="100">
                <template #default="{ row }"><el-input-number v-model="row.quantity" class="full" :min="0.01" :precision="2" :controls="false" /></template>
              </el-table-column>
              <el-table-column label="单位" width="90">
                <template #default="{ row }"><el-input v-model="row.unit" /></template>
              </el-table-column>
              <el-table-column label="操作" width="80" align="center">
                <template #default="{ $index }"><el-button link type="danger" @click="removeItem($index)">删除</el-button></template>
              </el-table-column>
            </el-table>
            <el-button class="add-item" @click="addItem">+ 添加药品</el-button>
            <div class="item-tip">金额由后端按药品单价 × 数量计算，请务必选择药品。</div>
          </el-form-item>

          <el-form-item>
            <el-button type="primary" :loading="submitting" @click="submitPrescription">提交处方</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- 4. 检验录入 -->
      <el-tab-pane label="检验录入" name="lab">
        <el-alert v-if="!currentDoctor" class="tab-guard" type="info" show-icon :closable="false" title="请先选择医生（登录）" />
        <el-alert v-else-if="!currentPatient || !currentVisit" class="tab-guard" type="info" show-icon :closable="false" title="请先选择患者和就诊" />
        <el-form v-else ref="labFormRef" :model="labForm" :rules="labRules" label-width="96px" class="entry-form">
          <el-row :gutter="16">
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
                <el-date-picker v-model="labForm.testedAt" class="full" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" placeholder="选择检验时间" />
              </el-form-item>
            </el-col>
            <el-col :span="24">
              <el-form-item label="结果判定" prop="isAbnormal">
                <el-switch v-model="labForm.isAbnormal" :active-value="1" :inactive-value="0" active-text="异常" inactive-text="正常" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item>
            <el-button type="primary" :loading="submitting" @click="submitLab">提交检验</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- 5. 住院登记（独立表单，直接填患者 ID，不依赖当前接诊） -->
      <el-tab-pane label="住院登记" name="admission">
        <el-form ref="admissionFormRef" :model="admissionForm" :rules="admissionRules" label-width="96px" class="entry-form">
          <el-row :gutter="16">
            <el-col :xs="24" :md="12">
              <el-form-item label="患者 ID" prop="patientId">
                <el-input-number v-model="admissionForm.patientId" class="full" :min="1" :precision="0" :controls="false" placeholder="必填" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="住院号" prop="admissionNo">
                <el-input v-model="admissionForm.admissionNo" placeholder="如 A10001-1" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="科室" prop="department">
                <el-input v-model="admissionForm.department" placeholder="如 内科" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="医生" prop="doctor">
                <el-input v-model="admissionForm.doctor" placeholder="如 张三" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="床位" prop="bedNo">
                <el-input v-model="admissionForm.bedNo" placeholder="如 12床" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="入院诊断" prop="diagnosis">
                <el-input v-model="admissionForm.diagnosis" placeholder="如 上呼吸道感染" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="入院时间" prop="admissionTime">
                <el-date-picker v-model="admissionForm.admissionTime" class="full" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" placeholder="选择入院时间" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item>
            <el-button type="primary" :loading="submitting" @click="submitAdmission">提交住院登记</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- 6. 收费录入（独立表单） -->
      <el-tab-pane label="收费" name="charge">
        <el-form ref="chargeFormRef" :model="chargeForm" :rules="chargeRules" label-width="96px" class="entry-form">
          <el-row :gutter="16">
            <el-col :xs="24" :md="12">
              <el-form-item label="患者 ID" prop="patientId">
                <el-input-number v-model="chargeForm.patientId" class="full" :min="1" :precision="0" :controls="false" placeholder="必填" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="就诊 ID" prop="visitId">
                <el-input-number v-model="chargeForm.visitId" class="full" :min="1" :precision="0" :controls="false" placeholder="必填" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="收费类型" prop="itemType">
                <el-select v-model="chargeForm.itemType" class="full">
                  <el-option label="挂号费" :value="0" />
                  <el-option label="药费" :value="1" />
                  <el-option label="检验费" :value="2" />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="收费项目" prop="itemName">
                <el-input v-model="chargeForm.itemName" placeholder="如 门诊挂号费" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="收费单号" prop="chargeNo">
                <el-input v-model="chargeForm.chargeNo" placeholder="如 C10001-1" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="金额（元）" prop="amount">
                <el-input-number v-model="chargeForm.amount" class="full" :min="0" :precision="2" :controls="false" placeholder="必填" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="收费时间" prop="chargedAt">
                <el-date-picker v-model="chargeForm.chargedAt" class="full" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" placeholder="选择收费时间" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item>
            <el-button type="primary" :loading="submitting" @click="submitCharge">提交收费</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- 7. 发药录入（独立表单） -->
      <el-tab-pane label="发药" name="dispense">
        <el-form ref="dispenseFormRef" :model="dispenseForm" :rules="dispenseRules" label-width="96px" class="entry-form">
          <el-row :gutter="16">
            <el-col :xs="24" :md="12">
              <el-form-item label="患者 ID" prop="patientId">
                <el-input-number v-model="dispenseForm.patientId" class="full" :min="1" :precision="0" :controls="false" placeholder="必填" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="就诊 ID" prop="visitId">
                <el-input-number v-model="dispenseForm.visitId" class="full" :min="1" :precision="0" :controls="false" placeholder="必填" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="处方 ID" prop="prescriptionId">
                <el-input-number v-model="dispenseForm.prescriptionId" class="full" :min="1" :precision="0" :controls="false" placeholder="必填" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="药品名称" prop="drugName">
                <el-input v-model="dispenseForm.drugName" placeholder="如 阿莫西林胶囊" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="数量" prop="quantity">
                <el-input-number v-model="dispenseForm.quantity" class="full" :min="0.01" :precision="2" :controls="false" placeholder="可选" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item label="发药时间" prop="dispensedAt">
                <el-date-picker v-model="dispenseForm.dispensedAt" class="full" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" placeholder="选择发药时间" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item>
            <el-button type="primary" :loading="submitting" @click="submitDispense">提交发药</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>
    </el-tabs>
  </el-card>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { createVisit, createDiagnosis, createPrescription, createLabTest, listDoctors, listVisits } from '../api/business'
import { createAdmission } from '../api/inpatient'
import { createCharge, createDispense } from '../api/charge'
import { listPatients } from '../api/patient'

const router = useRouter()

const activeTab = ref('visit')
const submitting = ref(false)
const lastPatientId = ref(null)

// 药品字典：后端未提供 /api/drugs 接口，硬编码 seed 的 12 条药品（id 即插入顺序）
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

const nowIso = () => {
  const d = new Date()
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

// ---- 医生身份（轻量登录） ----
const doctors = ref([])
const currentDoctor = ref(null)
const groupedDoctors = computed(() => {
  const map = new Map()
  for (const d of doctors.value) {
    if (!map.has(d.department)) map.set(d.department, [])
    map.get(d.department).push(d)
  }
  return [...map.entries()].map(([department, ds]) => ({ department, doctors: ds }))
})

// ---- 当前接诊 ----
const currentPatient = ref(null)
const currentVisit = ref(null)
const patientOptions = ref([])
const visitOptions = ref([])
const patientLoading = ref(false)

const genderText = (g) => (g === 0 ? '男' : g === 1 ? '女' : '-')
const ageText = (birthDate) => {
  if (!birthDate) return '-'
  const birth = new Date(String(birthDate).replace(/-/g, '/'))
  if (Number.isNaN(birth.getTime())) return '-'
  const today = new Date()
  let age = today.getFullYear() - birth.getFullYear()
  const monthDiff = today.getMonth() - birth.getMonth()
  if (monthDiff < 0 || (monthDiff === 0 && today.getDate() < birth.getDate())) age -= 1
  return age >= 0 ? `${age}岁` : '-'
}
const formatDateTime = (t) => (t ? String(t).replace('T', ' ').slice(0, 19) : '')

const patientLabel = (p) => `${p.name}｜${genderText(p.gender)} ${ageText(p.birthDate)}｜${p.idCard || '-'}`
const visitLabel = (v) => `${v.visitNo} · ${v.department} · ${formatDateTime(v.visitTime)}`

const searchPatients = async (keyword) => {
  if (!currentDoctor.value) return
  patientLoading.value = true
  try {
    const res = await listPatients({ department: currentDoctor.value.department, keyword: keyword || '', size: 20 })
    patientOptions.value = res.data.records || []
  } finally {
    patientLoading.value = false
  }
}

const onDoctorChange = () => {
  currentPatient.value = null
  currentVisit.value = null
  patientOptions.value = []
  visitOptions.value = []
}

const onPatientChange = async () => {
  currentVisit.value = null
  visitOptions.value = []
  if (!currentPatient.value) return
  const res = await listVisits({ patientId: currentPatient.value.id, department: currentDoctor.value.department })
  visitOptions.value = res.data || []
}

// ---- 表单 ----
const newVisitForm = () => ({
  patientId: null, visitNo: '', department: '', doctor: '', visitTime: nowIso(), status: 0, remark: ''
})
const newDiagnosisForm = () => ({ icdCode: '', diagnosisName: '', diagnosedAt: nowIso() })
const newItem = () => ({ drugId: null, drugName: '', dosage: '', frequency: '', days: 3, quantity: 1, unit: '' })
const newPrescriptionForm = () => ({ prescribedAt: nowIso(), items: [newItem()] })
const newLabForm = () => ({
  itemCode: '', itemName: '', resultValue: '', unit: '', referenceRange: '', isAbnormal: 0, testedAt: nowIso()
})
// 住院 / 收费 / 发药是独立表单：直接填 patientId（收费、发药另填 visitId 等外键），
// 不依赖「当前医生 / 当前患者」上下文，因此无需先在顶部选医生。
const newAdmissionForm = () => ({
  patientId: null, admissionNo: '', department: '', doctor: '',
  bedNo: '', diagnosis: '', admissionTime: nowIso()
})
const newChargeForm = () => ({
  patientId: null, visitId: null, itemType: 0, itemName: '',
  chargeNo: '', amount: null, chargedAt: nowIso()
})
const newDispenseForm = () => ({
  patientId: null, visitId: null, prescriptionId: null,
  drugName: '', quantity: null, dispensedAt: nowIso()
})

const visitForm = ref(newVisitForm())
const diagnosisForm = ref(newDiagnosisForm())
const prescriptionForm = ref(newPrescriptionForm())
const labForm = ref(newLabForm())
const admissionForm = ref(newAdmissionForm())
const chargeForm = ref(newChargeForm())
const dispenseForm = ref(newDispenseForm())

const visitFormRef = ref()
const diagnosisFormRef = ref()
const prescriptionFormRef = ref()
const labFormRef = ref()
const admissionFormRef = ref()
const chargeFormRef = ref()
const dispenseFormRef = ref()

const required = (message) => ({ required: true, message, trigger: ['blur', 'change'] })

const visitRules = {
  patientId: [required('请输入患者 ID')],
  visitNo: [required('请输入就诊号')],
  visitTime: [required('请选择就诊时间')]
}
const diagnosisRules = {
  diagnosisName: [required('请输入诊断名称')],
  diagnosedAt: [required('请选择诊断时间')]
}
const prescriptionRules = {
  prescribedAt: [required('请选择开方时间')]
}
const labRules = {
  itemName: [required('请输入检验项目名称')],
  testedAt: [required('请选择检验时间')]
}
// 只校验 NOT NULL 列：admission(patient_id/admission_no/admission_time)、
// charge(patient_id/visit_id/charge_no/charged_at)、dispense(patient_id/visit_id/prescription_id/dispensed_at)
const admissionRules = {
  patientId: [required('请输入患者 ID')],
  admissionNo: [required('请输入住院号')],
  admissionTime: [required('请选择入院时间')]
}
const chargeRules = {
  patientId: [required('请输入患者 ID')],
  visitId: [required('请输入就诊 ID')],
  chargeNo: [required('请输入收费单号')],
  amount: [required('请输入金额')],
  chargedAt: [required('请选择收费时间')]
}
const dispenseRules = {
  patientId: [required('请输入患者 ID')],
  visitId: [required('请输入就诊 ID')],
  prescriptionId: [required('请输入处方 ID')],
  dispensedAt: [required('请选择发药时间')]
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
  const payload = {
    patientId: currentPatient.value.id,
    visitId: currentVisit.value.id,
    doctor: currentDoctor.value.name,
    ...diagnosisForm.value
  }
  if (await submit(() => createDiagnosis(payload), '诊断录入成功', payload.patientId)) {
    diagnosisForm.value = newDiagnosisForm()
  }
}

const addItem = () => prescriptionForm.value.items.push(newItem())
const removeItem = (index) => prescriptionForm.value.items.splice(index, 1)

const onDrugChange = (row) => {
  const drug = DRUGS.find((d) => d.id === row.drugId)
  if (!drug) return
  row.drugName = drug.name
  row.unit = drug.unit
}

const submitPrescription = async () => {
  if (!(await validateForm(prescriptionFormRef))) return
  const rows = prescriptionForm.value.items
  if (!rows.length) { ElMessage.warning('请至少添加一条药品明细'); return }
  if (rows.some((i) => !i.drugId)) { ElMessage.warning('每条明细都必须选择药品'); return }
  const payload = {
    patientId: currentPatient.value.id,
    visitId: currentVisit.value.id,
    doctor: currentDoctor.value.name,
    prescribedAt: prescriptionForm.value.prescribedAt,
    items: rows.map((i) => ({
      drugId: i.drugId,
      drugName: i.drugName,
      dosage: i.dosage,
      frequency: i.frequency,
      days: i.days,
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
  const payload = { patientId: currentPatient.value.id, visitId: currentVisit.value.id, ...labForm.value }
  if (await submit(() => createLabTest(payload), '检验录入成功', payload.patientId)) {
    labForm.value = newLabForm()
  }
}

const submitAdmission = async () => {
  if (!(await validateForm(admissionFormRef))) return
  // status 有 DB 默认值，但显式给 0（在院）语义更清晰
  const payload = { ...admissionForm.value, status: 0 }
  if (await submit(() => createAdmission(payload), '住院登记成功', payload.patientId)) {
    admissionForm.value = newAdmissionForm()
  }
}

const submitCharge = async () => {
  if (!(await validateForm(chargeFormRef))) return
  const payload = { ...chargeForm.value }
  if (await submit(() => createCharge(payload), '收费录入成功', payload.patientId)) {
    chargeForm.value = newChargeForm()
  }
}

const submitDispense = async () => {
  if (!(await validateForm(dispenseFormRef))) return
  const payload = { ...dispenseForm.value }
  if (await submit(() => createDispense(payload), '发药录入成功', payload.patientId)) {
    dispenseForm.value = newDispenseForm()
  }
}

const goTimeline = () => router.push('/patient/' + lastPatientId.value)

onMounted(async () => {
  const res = await listDoctors()
  doctors.value = res.data || []
})
</script>

<style scoped>
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}
.page-title { margin: 0; font-size: 18px; font-weight: 600; color: #303133; }
.page-tip { margin: 4px 0 0; font-size: 13px; color: #909399; }
.entry-form { max-width: 900px; }
.full { width: 100%; }
.item-table { width: 100%; }
.add-item { margin-top: 8px; }
.item-tip { margin-top: 4px; font-size: 12px; color: #909399; }

.doctor-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  margin-bottom: 12px;
  background: #f5f7fa;
  border-radius: 6px;
}
.bar-label { font-size: 14px; font-weight: 600; color: #303133; }
.doctor-select { width: 240px; }
.doctor-dept { font-size: 13px; color: #606266; }

.reception-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 12px;
}
.reception-select { width: 320px; }
.ctx-line { font-size: 13px; color: #303133; }
.muted { color: #909399; }
.tab-guard { margin-bottom: 12px; }
</style>
