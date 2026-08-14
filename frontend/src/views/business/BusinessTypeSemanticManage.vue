<template>
  <div class="business-type-page">
    <div class="page-header">
      <div>
        <h3>业务语义管理</h3>
        <p>维护供应链、营销等业务类型及其建模语义。业务分析域选择类型后，Guide 只提供该类型定义的语义模式供用户选择。</p>
      </div>
      <el-button type="primary" @click="openCreateDialog">新建业务类型</el-button>
    </div>

    <el-table :data="businessTypes" border stripe v-loading="loading">
      <el-table-column prop="type_name" label="业务类型" min-width="150" />
      <el-table-column prop="type_code" label="编码" min-width="130" />
      <el-table-column prop="semantic_desc" label="业务语义描述" min-width="360" show-overflow-tooltip />
      <el-table-column label="语义模式" min-width="240">
        <template #default="{ row }">
          <el-tag v-for="pattern in row.semantic_patterns || []" :key="pattern.pattern_code" size="small" class="pattern-tag">
            {{ pattern.pattern_name }}
          </el-tag>
          <span v-if="!(row.semantic_patterns || []).length" class="muted">未配置</span>
        </template>
      </el-table-column>
      <el-table-column prop="status" label="状态" width="100">
        <template #default="{ row }"><el-tag :type="row.status === 'ACTIVE' ? 'success' : 'info'" size="small">{{ row.status === 'ACTIVE' ? '启用' : '停用' }}</el-tag></template>
      </el-table-column>
      <el-table-column label="操作" width="170" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" @click="openEditDialog(row)">编辑</el-button>
          <el-popconfirm title="确定删除该业务类型？已被业务分析域使用的类型不能删除。" @confirm="removeBusinessType(row.type_code)">
            <template #reference><el-button size="small" type="danger">删除</el-button></template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" :title="isEditing ? '编辑业务类型语义' : '新建业务类型语义'" width="940px" destroy-on-close>
      <el-form ref="formRef" :model="form" :rules="rules" label-width="110px">
        <el-form-item label="类型名称" prop="type_name"><el-input v-model="form.type_name" placeholder="例如：供应链域" /></el-form-item>
        <el-form-item label="类型编码" prop="type_code">
          <el-input v-model="form.type_code" :disabled="isEditing" placeholder="例如：SUPPLY_CHAIN，仅支持大写字母、数字和下划线" />
        </el-form-item>
        <el-form-item label="业务语义描述"><el-input v-model="form.semantic_desc" type="textarea" :rows="3" placeholder="说明该类型的核心业务对象、关系边界和建模目标" /></el-form-item>
        <el-form-item label="状态"><el-radio-group v-model="form.status"><el-radio value="ACTIVE">启用</el-radio><el-radio value="INACTIVE">停用</el-radio></el-radio-group></el-form-item>
        <el-form-item label="语义模式">
          <div class="pattern-editor">
            <div class="pattern-help">每项模式会作为 Guide 的可选建模策略。请用具体业务语义说明引导实体与关系生成，不依赖固定表名、表角色或预设派生对象。</div>
            <el-table :data="form.semantic_patterns" border size="small">
              <el-table-column label="编码" min-width="130"><template #default="{ row }"><el-input v-model="row.pattern_code" placeholder="如 order-flow" /></template></el-table-column>
              <el-table-column label="模式名称" min-width="130"><template #default="{ row }"><el-input v-model="row.pattern_name" placeholder="如订单流转" /></template></el-table-column>
              <el-table-column label="业务语义说明" min-width="220"><template #default="{ row }"><el-input v-model="row.description" placeholder="指导实体和关系生成" /></template></el-table-column>
              <el-table-column label="" width="64"><template #default="{ $index }"><el-button link type="danger" @click="removePattern($index)">删除</el-button></template></el-table-column>
            </el-table>
            <el-button plain type="primary" size="small" class="add-pattern" @click="addPattern">+ 添加语义模式</el-button>
          </div>
        </el-form-item>
      </el-form>
      <template #footer><el-button @click="dialogVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveBusinessType">保存</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { businessTypeApi } from '../../api'

const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const isEditing = ref(false)
const editingCode = ref('')
const formRef = ref()
const businessTypes = ref<any[]>([])
const createPattern = () => ({ pattern_code: '', pattern_name: '', description: '' })
const form = reactive({ type_code: '', type_name: '', semantic_desc: '', status: 'ACTIVE', semantic_patterns: [] as any[] })
const rules = {
  type_name: [{ required: true, message: '请输入业务类型名称', trigger: 'blur' }],
  type_code: [
    { required: true, message: '请输入业务类型编码', trigger: 'blur' },
    { pattern: /^[A-Za-z][A-Za-z0-9_]*$/, message: '编码仅支持字母、数字和下划线，且以字母开头', trigger: 'blur' }
  ]
}

const toEditorPattern = (item: any) => ({
  pattern_code: item.pattern_code || '', pattern_name: item.pattern_name || '', description: item.description || ''
})
const resetForm = () => Object.assign(form, { type_code: '', type_name: '', semantic_desc: '', status: 'ACTIVE', semantic_patterns: [] })
const loadBusinessTypes = async () => {
  loading.value = true
  try { const res = await businessTypeApi.list(); businessTypes.value = res.data || [] } finally { loading.value = false }
}
const openCreateDialog = () => { isEditing.value = false; editingCode.value = ''; resetForm(); dialogVisible.value = true }
const openEditDialog = (row: any) => {
  isEditing.value = true; editingCode.value = row.type_code
  Object.assign(form, { type_code: row.type_code, type_name: row.type_name, semantic_desc: row.semantic_desc || '', status: row.status || 'ACTIVE', semantic_patterns: (row.semantic_patterns || []).map(toEditorPattern) })
  dialogVisible.value = true
}
const addPattern = () => form.semantic_patterns.push(createPattern())
const removePattern = (index: number) => form.semantic_patterns.splice(index, 1)
const saveBusinessType = async () => {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  const patterns = form.semantic_patterns.map(item => ({
    pattern_code: item.pattern_code.trim().toLowerCase(), pattern_name: item.pattern_name.trim(), description: item.description?.trim() || ''
  }))
  if (patterns.some(item => !item.pattern_code || !item.pattern_name)) { ElMessage.warning('每个语义模式都需要填写编码和名称'); return }
  if (new Set(patterns.map(item => item.pattern_code)).size !== patterns.length) { ElMessage.warning('语义模式编码不能重复'); return }
  saving.value = true
  try {
    const payload = { type_code: form.type_code.trim().toUpperCase(), type_name: form.type_name.trim(), semantic_desc: form.semantic_desc.trim(), status: form.status, semantic_patterns: patterns }
    if (isEditing.value) await businessTypeApi.update(editingCode.value, payload)
    else await businessTypeApi.create(payload)
    ElMessage.success('业务类型语义已保存'); dialogVisible.value = false; await loadBusinessTypes()
  } finally { saving.value = false }
}
const removeBusinessType = async (code: string) => { await businessTypeApi.delete(code); ElMessage.success('业务类型已删除'); await loadBusinessTypes() }
onMounted(loadBusinessTypes)
</script>

<style scoped>
.business-type-page { padding: 0; }
.page-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 16px; }
.page-header h3 { margin: 0 0 6px; color: #1a3a5c; font-size: 18px; }
.page-header p { margin: 0; color: #6b7280; line-height: 1.6; }
.pattern-tag { margin: 2px 6px 2px 0; }
.muted, .pattern-help { color: #8a96a3; font-size: 12px; }
.pattern-editor { width: 100%; }
.pattern-help { margin-bottom: 10px; line-height: 1.6; }
.add-pattern { margin-top: 10px; }
</style>
