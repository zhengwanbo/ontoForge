<template>
  <div class="business-domain-page">
    <div class="page-header">
      <div>
        <h3>业务分析域管理</h3>
        <p>维护业务分析域名称、类型、描述和启用状态，作为源数据浏览、本体构建和数据映射的统一隔离上下文。</p>
      </div>
      <el-button type="primary" @click="openCreateDialog">新建业务分析域</el-button>
    </div>

    <el-table :data="domains" border stripe v-loading="loading">
      <el-table-column prop="domain_name" label="业务分析域名称" min-width="220" />
      <el-table-column prop="domain_type" label="类型" width="140">
        <template #default="{ row }">
          <el-tag size="small" type="success">{{ formatType(row.domain_type) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="domain_desc" label="描述" min-width="260" show-overflow-tooltip />
      <el-table-column prop="status" label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="row.status === 'ACTIVE' ? 'success' : 'info'" size="small">
            {{ row.status === 'ACTIVE' ? '启用' : '停用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_by" label="创建人" width="120" />
      <el-table-column label="操作" width="260" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" @click="openEditDialog(row)">编辑</el-button>
          <el-button size="small" @click="setAsCurrent(row)">设为当前域</el-button>
          <el-popconfirm title="确定删除该业务分析域？" @confirm="deleteDomain(row.domain_id)">
            <template #reference>
              <el-button size="small" type="danger">删除</el-button>
            </template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" :title="isEditing ? '编辑业务分析域' : '新建业务分析域'" width="560px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="110px">
        <el-form-item label="名称" prop="domain_name">
          <el-input v-model="form.domain_name" placeholder="例如：质量缺陷分析域" />
        </el-form-item>
        <el-form-item label="类型" prop="domain_type">
          <el-select v-model="form.domain_type" placeholder="选择业务分析域类型">
            <el-option v-for="item in domainTypeOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.domain_desc" type="textarea" :rows="4" placeholder="说明该业务分析域承载的分析对象和边界" />
        </el-form-item>
        <el-form-item v-if="isEditing" label="状态">
          <el-radio-group v-model="form.status">
            <el-radio value="ACTIVE">启用</el-radio>
            <el-radio value="INACTIVE">停用</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveDomain">{{ isEditing ? '保存' : '创建' }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { domainApi } from '../../api'
import { useAppStore } from '../../stores/app'

const appStore = useAppStore()

const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const isEditing = ref(false)
const editingId = ref('')
const formRef = ref()
const domains = ref<any[]>([])

const domainTypeOptions = [
  { label: '业务主题域', value: 'BUSINESS' },
  { label: '制造对象域', value: 'OBJECT' },
  { label: '分析场景域', value: 'SCENARIO' },
  { label: '自定义域', value: 'CUSTOM' }
]

const form = reactive({
  domain_name: '',
  domain_type: 'BUSINESS',
  domain_desc: '',
  status: 'ACTIVE'
})

const rules = {
  domain_name: [{ required: true, message: '请输入业务分析域名称', trigger: 'blur' }],
  domain_type: [{ required: true, message: '请选择业务分析域类型', trigger: 'change' }]
}

const formatType = (value: string) => domainTypeOptions.find(item => item.value === value)?.label || value || '未分类'
const normalizeDomainName = (value: string) => value.trim()

const resetForm = () => {
  Object.assign(form, {
    domain_name: '',
    domain_type: 'BUSINESS',
    domain_desc: '',
    status: 'ACTIVE'
  })
}

const loadDomains = async () => {
  loading.value = true
  try {
    const res = await domainApi.list()
    domains.value = res.data || []
  } finally {
    loading.value = false
  }
}

const openCreateDialog = () => {
  isEditing.value = false
  editingId.value = ''
  resetForm()
  dialogVisible.value = true
}

const openEditDialog = (row: any) => {
  isEditing.value = true
  editingId.value = row.domain_id
  Object.assign(form, {
    domain_name: row.domain_name,
    domain_type: row.domain_type || 'BUSINESS',
    domain_desc: row.domain_desc || '',
    status: row.status || 'ACTIVE'
  })
  dialogVisible.value = true
}

const saveDomain = async () => {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  const normalizedName = normalizeDomainName(form.domain_name)
  if (!normalizedName) {
    ElMessage.warning('请输入业务分析域名称')
    return
  }

  const duplicated = domains.value.find(item =>
    item.domain_name === normalizedName && (!isEditing.value || item.domain_id !== editingId.value)
  )
  if (duplicated) {
    ElMessage.error(`业务分析域名称已存在：${normalizedName}`)
    return
  }

  saving.value = true
  try {
    const payload = {
      ...form,
      domain_name: normalizedName
    }
    if (isEditing.value) {
      await domainApi.update(editingId.value, payload)
      ElMessage.success('业务分析域已更新')
    } else {
      await domainApi.create(payload)
      ElMessage.success('业务分析域已创建')
    }
    dialogVisible.value = false
    await loadDomains()
  } finally {
    saving.value = false
  }
}

const setAsCurrent = (row: any) => {
  appStore.setCurrentDomain(row.domain_id, row.domain_name)
  ElMessage.success(`当前业务分析域已切换为 ${row.domain_name}`)
}

const deleteDomain = async (domainId: string) => {
  await domainApi.delete(domainId)
  if (appStore.currentDomainId === domainId) {
    appStore.setCurrentDomain('', '')
  }
  ElMessage.success('业务分析域已删除')
  await loadDomains()
}

onMounted(() => {
  loadDomains()
})
</script>

<style scoped>
.business-domain-page {
  padding: 0;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 16px;
}

.page-header h3 {
  margin: 0 0 6px;
  color: #1a3a5c;
  font-size: 18px;
}

.page-header p {
  margin: 0;
  color: #6b7280;
  line-height: 1.6;
}
</style>
