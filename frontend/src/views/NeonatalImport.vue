<template>
  <div>
    <div class="page-heading"><div><div class="eyebrow">DATA IMPORT</div><h1>导入出院小结</h1><p>从 Excel 建立新生儿病案数据集，导入后可切换批次查看。</p></div></div>
    <div class="import-grid">
      <div class="panel">
        <div class="panel-title">上传 Excel <small>.xlsx / 最大 50 MB</small></div>
        <el-upload drag :auto-upload="false" :limit="1" accept=".xlsx" :disabled="importing" :on-change="select" :on-remove="()=>file=null" :on-exceed="()=>ElMessage.warning('请先移除已选文件')" v-model:file-list="files">
          <el-icon class="upload-icon"><UploadFilled/></el-icon><div class="el-upload__text">拖放出院小结到这里，或 <em>选择文件</em></div><template #tip><div class="muted">读取第一个工作表，支持调整列顺序，须保留 18 个字段表头。</div></template>
        </el-upload>
        <el-button type="primary" :loading="importing" :disabled="!file" @click="submit" style="margin-top:20px">{{importing?'正在校验并导入…':'开始导入'}}</el-button>
        <el-alert v-if="result" :title="result.reused?'该文件已导入，已切换到已有批次':'数据导入完成'" type="success" :closable="false" style="margin-top:20px"><p>批次 #{{result.id}}，共 {{result.recordCount.toLocaleString()}} 条小结，{{result.issueCount.toLocaleString()}} 项数据问题。</p><router-link to="/">查看病案 →</router-link></el-alert>
        <el-alert v-if="importError" title="导入失败" :description="importError" type="error" :closable="false" style="margin-top:20px"/>
      </div>
      <div class="panel"><div class="panel-title">数据处理规则</div><ul class="rules"><li>原始行、来源文档、工作表和行号完整保存。</li><li>同一文件通过 SHA-256 校验，重复上传不重复入库。</li><li>新文件作为独立批次保存，各批次独立统计；默认显示最近成功导入的批次。</li><li>重复住院号分别保留，不自动合并患儿。</li><li>无法识别的孕周和日期保留原文并标记待核对。</li><li>检查、医嘱与诊疗过程按原文保存，不推测处方和费用。</li><li>整个批次在事务内导入，失败不会留下部分病案。</li></ul></div>
    </div>
    <div class="panel"><div class="panel-title">导入历史 <el-button size="small" @click="reload">刷新</el-button></div><p v-if="historyError" class="error-state">导入历史加载失败，请刷新重试。</p><el-table v-else :data="batches"><el-table-column prop="id" label="批次" width="80"/><el-table-column prop="fileName" label="文件名" min-width="260"/><el-table-column prop="sheetName" label="工作表" width="100"/><el-table-column prop="recordCount" label="小结数" width="100"/><el-table-column prop="issueCount" label="问题数" width="100"/><el-table-column prop="importedAt" label="导入时间" min-width="180"/><el-table-column label="操作" width="100"><template #default="{row}"><el-button link type="primary" @click="show(row.id)">查看批次</el-button></template></el-table-column></el-table></div>
    <div class="panel"><div class="panel-title">必需表头</div><p class="muted">性别、年龄、住院号、入院时间、出院时间、入院诊断、出院诊断、入院时主要症状及体征、实验室检查及主要会诊、特殊检验及重要会诊、诊疗过程、合并症、出院时情况、出院医嘱、出院指导、治疗结果、来源文件、孕周。</p></div>
  </div>
</template>
<script setup>
import {ref,onMounted} from 'vue'
import {useRouter} from 'vue-router'
import {ElMessage} from 'element-plus'
import {UploadFilled} from '@element-plus/icons-vue'
import {batches,selectedBatch,refreshBatches,importWorkbook} from '../api/neonatal'
const router=useRouter(),file=ref(null),files=ref([]),importing=ref(false),result=ref(null),importError=ref(''),historyError=ref(false)
function select(item){if(!item.name.toLowerCase().endsWith('.xlsx')||item.size>50*1024*1024){ElMessage.error('请选择不超过 50 MB 的 .xlsx 文件');files.value=[];file.value=null;return}file.value=item.raw;result.value=null;importError.value=''}
async function reload(){historyError.value=false;try{await refreshBatches()}catch{historyError.value=true}}
async function submit(){if(!file.value)return;importing.value=true;result.value=null;importError.value=''
  try{const res=await importWorkbook(file.value);result.value=res.data;files.value=[];file.value=null;try{await refreshBatches(res.data.id);historyError.value=false}catch{historyError.value=true}}
  catch(e){importError.value=e.message||'导入失败，请核对表头和文件格式后重试。'}finally{importing.value=false}
}
function show(id){selectedBatch.value=id;router.push('/')}
onMounted(reload)
</script>
<style scoped>.import-grid{display:grid;grid-template-columns:1.2fr 1fr;gap:20px}.upload-icon{font-size:48px;color:#83aeb6;margin:15px 0}.rules{padding-left:20px;font-size:13px;line-height:2.3;color:#708594;margin:0}@media(max-width:1000px){.import-grid{grid-template-columns:1fr}}</style>
