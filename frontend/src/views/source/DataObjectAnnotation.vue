<template>
  <div class="annotation-page">
    <section class="domain-band">
      <span class="domain-label">当前业务分析域</span>
      <el-tag v-if="currentDomainName" type="success" effect="light">{{ currentDomainName }}</el-tag>
      <span v-else class="domain-empty">请先在顶部选择业务分析域</span>
    </section>

    <section class="toolbar-band">
      <div class="toolbar-row">
        <el-select
          v-model="selectedSourceId"
          placeholder="选择数据库连接"
          clearable
          filterable
          class="toolbar-select"
          @change="handleSourceChange"
        >
          <el-option
            v-for="source in dataSources"
            :key="source.source_id"
            :label="source.source_name"
            :value="source.source_id"
          >
            <div class="source-option">
              <span>{{ source.source_name }}</span>
              <span class="source-option-meta">{{ formatConnection(source) }}</span>
            </div>
          </el-option>
        </el-select>

        <el-select
          v-model="selectedSchema"
          placeholder="选择 Schema"
          filterable
          class="toolbar-select"
          :disabled="!selectedSourceId || schemaLoading"
          @change="handleSchemaChange"
        >
          <el-option v-for="schema in schemaOptions" :key="schema" :label="schema" :value="schema" />
        </el-select>

        <el-input
          v-model="prefixKeyword"
          placeholder="按表名前缀过滤"
          clearable
          class="toolbar-input"
          @keyup.enter="handleTableFilter"
          @clear="handleTableFilter"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>

        <el-button type="primary" :disabled="!selectedSourceId" @click="handleTableFilter">查询表</el-button>
      </div>
    </section>

    <div class="content-layout">
      <aside class="table-panel" v-loading="tableLoading">
        <div class="panel-head">
          <span class="panel-title">待标注数据表</span>
          <span class="panel-meta" v-if="selectedSchema">{{ selectedSchema }}</span>
        </div>
        <div class="table-list" v-if="tables.length > 0">
          <button
            v-for="table in tables"
            :key="`${table.owner}.${table.table_name}`"
            class="table-item"
            :class="{ active: table.table_name === selectedTableName }"
            @click="selectTable(table)"
          >
            <div class="table-item-head">
              <span class="table-name">{{ table.table_name }}</span>
              <el-tag size="small" type="info">{{ table.num_rows ?? 0 }}</el-tag>
            </div>
            <div class="table-comment">{{ table.comments || '当前无表描述' }}</div>
          </button>
        </div>
        <el-empty v-else :description="selectedSourceId ? '未找到匹配的表' : currentDomainId ? '请先选择数据库连接' : '请先选择业务分析域'" />
      </aside>

      <main class="detail-panel">
        <template v-if="selectedTableName && tableDetail">
          <section class="detail-header">
            <div>
              <h3>{{ tableDetail.table_name }}</h3>
              <div class="detail-meta">
                <span>Schema：{{ tableDetail.owner }}</span>
                <span>字段数：{{ tableDetail.columns.length }}</span>
                <span>样例数据：前 {{ tableDetail.sample_limit }} 行</span>
              </div>
            </div>
            <div class="detail-actions">
              <el-select
                v-model="primaryModelConfigId"
                filterable
                placeholder="选择生成主模型"
                class="model-select"
              >
                <el-option
                  v-for="model in llmConfigs"
                  :key="model.config_id"
                  :label="formatModelOption(model)"
                  :value="model.config_id"
                />
              </el-select>
              <el-select
                v-model="verifierModelConfigId"
                filterable
                placeholder="选择校验模型"
                class="model-select"
              >
                <el-option
                  v-for="model in verifierOptions"
                  :key="model.config_id"
                  :label="formatModelOption(model)"
                  :value="model.config_id"
                />
              </el-select>
              <el-button :icon="RefreshRight" @click="refreshCurrent">刷新结构</el-button>
              <el-button
                type="primary"
                :loading="generateLoading"
                :disabled="llmConfigs.length < 2"
                @click="generateComments"
              >
                大模型补全描述
              </el-button>
              <el-button type="success" :loading="saveLoading" @click="saveComments">保存到数据库 Comments</el-button>
            </div>
          </section>

          <section class="status-band" v-if="generationMode">
            <el-tag size="small" :type="generationMode === 'llm' ? 'success' : 'warning'">
              {{ generationMode === 'llm' ? '主模型生成' : '回退规则生成' }}
            </el-tag>
            <el-tag v-if="verificationMode === 'llm'" size="small" type="primary">校验模型复核</el-tag>
            <span v-if="primaryModelLabel">生成模型：{{ primaryModelLabel }}</span>
            <span v-if="verifierModelLabel">校验模型：{{ verifierModelLabel }}</span>
            <span>仅对 comments 为空的表和字段补全建议，保存前可手工调整。</span>
          </section>
          <section class="status-band warning-band" v-if="llmConfigs.length < 2">
            <el-tag size="small" type="warning">模型数量不足</el-tag>
            <span>当前至少需要两个启用的大模型配置，才能使用“主模型生成 + 校验模型复核”的双模型标注流程。</span>
          </section>

          <section class="editor-band">
            <div class="editor-title">表描述</div>
            <el-form label-width="92px">
              <el-form-item label="当前 Comments">
                <el-input :model-value="tableDetail.table_comment || ''" disabled type="textarea" :rows="2" />
              </el-form-item>
              <el-form-item label="最终 Comments">
                <el-input v-model="tableCommentDraft" type="textarea" :rows="2" placeholder="可修改表描述" />
              </el-form-item>
            </el-form>
          </section>

          <section class="editor-band">
            <div class="editor-title">字段描述校验</div>
            <el-table :data="columnDrafts" border stripe size="small" max-height="420">
              <el-table-column prop="column_name" label="字段名" min-width="160" />
              <el-table-column prop="data_type" label="类型" min-width="140" />
              <el-table-column prop="default_value" label="缺省值" min-width="120" show-overflow-tooltip />
              <el-table-column prop="current_comment" label="当前描述" min-width="180" show-overflow-tooltip />
              <el-table-column prop="suggested_comment" label="建议描述" min-width="220" show-overflow-tooltip />
              <el-table-column label="最终描述" min-width="260">
                <template #default="{ row }">
                  <el-input v-model="row.final_comment" placeholder="可手工修正后保存" />
                </template>
              </el-table-column>
            </el-table>
          </section>

          <section class="editor-band">
            <div class="editor-title">样例数据</div>
            <el-table :data="tableDetail.sample_rows" border stripe size="small" max-height="280">
              <el-table-column
                v-for="column in tableDetail.sample_columns"
                :key="column"
                :prop="column"
                :label="column"
                min-width="140"
                show-overflow-tooltip
              />
            </el-table>
            <el-empty v-if="tableDetail.sample_rows.length === 0" description="当前表没有可展示的样例数据" />
          </section>
        </template>

        <el-empty v-else :description="currentDomainId ? '请选择需要标注的数据表' : '请先选择业务分析域'" />
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { RefreshRight, Search } from '@element-plus/icons-vue'
import { sourceApi, systemApi } from '../../api'
import { useAppStore } from '../../stores/app'

interface BrowseDataSource {
  source_id: string
  source_name: string
  is_default?: string
  schema_name?: string | null
  username: string
  host: string
  port: number
  service_name?: string | null
  sid?: string | null
}

interface BrowseTable {
  owner: string
  table_name: string
  comments?: string | null
  num_rows?: number | null
}

interface LLMConfigOption {
  config_id: string
  config_name: string
  model_name: string
  is_active: string
  is_default: string
}

interface TableColumn {
  column_name: string
  data_type: string
  default_value?: string | null
  comments?: string | null
}

interface TableDetail {
  owner: string
  table_name: string
  table_comment?: string | null
  columns: TableColumn[]
  sample_columns: string[]
  sample_rows: Record<string, unknown>[]
  sample_limit: number
}

interface ColumnDraft extends TableColumn {
  current_comment: string
  suggested_comment: string
  final_comment: string
}

const dataSources = ref<BrowseDataSource[]>([])
const schemaOptions = ref<string[]>([])
const tables = ref<BrowseTable[]>([])
const llmConfigs = ref<LLMConfigOption[]>([])

const selectedSourceId = ref('')
const selectedSchema = ref('')
const selectedTableName = ref('')
const prefixKeyword = ref('')
const primaryModelConfigId = ref('')
const verifierModelConfigId = ref('')

const tableDetail = ref<TableDetail | null>(null)
const tableCommentDraft = ref('')
const columnDrafts = ref<ColumnDraft[]>([])
const generationMode = ref('')
const verificationMode = ref('')
const primaryModelLabel = ref('')
const verifierModelLabel = ref('')

const schemaLoading = ref(false)
const tableLoading = ref(false)
const detailLoading = ref(false)
const generateLoading = ref(false)
const saveLoading = ref(false)
const appStore = useAppStore()
const verifierOptions = computed(() =>
  llmConfigs.value.filter(item => item.config_id !== primaryModelConfigId.value)
)
const currentDomainId = computed(() => appStore.currentDomainId)
const currentDomainName = computed(() => appStore.currentDomainName)

watch(primaryModelConfigId, value => {
  if (verifierModelConfigId.value === value) {
    verifierModelConfigId.value = verifierOptions.value[0]?.config_id || ''
  }
})

const formatConnection = (source: BrowseDataSource) => {
  const target = source.service_name ? `/${source.service_name}` : source.sid ? `:${source.sid}` : ''
  return `${source.host}:${source.port}${target} (${source.username})`
}

const formatModelOption = (model: LLMConfigOption) => `${model.config_name} / ${model.model_name}`

const resetSelection = () => {
  tables.value = []
  selectedTableName.value = ''
  tableDetail.value = null
  tableCommentDraft.value = ''
  columnDrafts.value = []
  generationMode.value = ''
  verificationMode.value = ''
  primaryModelLabel.value = ''
  verifierModelLabel.value = ''
}

const syncDraftsFromDetail = (detail: any) => {
  tableDetail.value = detail
  tableCommentDraft.value = detail?.table_comment || detail?.final_table_comment || ''
  columnDrafts.value = (detail?.columns || []).map((column: any) => ({
    ...column,
    current_comment: column.current_comment ?? column.comments ?? '',
    suggested_comment: column.suggested_comment ?? '',
    final_comment: column.final_comment ?? column.comments ?? ''
  }))
  generationMode.value = detail?.generation_mode || ''
  verificationMode.value = detail?.verification_mode || ''
  primaryModelLabel.value = detail?.primary_model ? `${detail.primary_model.config_name} / ${detail.primary_model.model_name}` : ''
  verifierModelLabel.value = detail?.verifier_model ? `${detail.verifier_model.config_name} / ${detail.verifier_model.model_name}` : ''
}

const loadDataSources = async () => {
  if (!currentDomainId.value) {
    dataSources.value = []
    selectedSourceId.value = ''
    schemaOptions.value = []
    selectedSchema.value = ''
    resetSelection()
    return
  }

  const res = await sourceApi.listDataSources(currentDomainId.value)
  dataSources.value = res.data || []
  if (!dataSources.value.length) return

  if (!selectedSourceId.value) {
    const defaultSource = dataSources.value.find(item => item.is_default === 'Y') || dataSources.value[0]
    selectedSourceId.value = defaultSource.source_id
    await handleSourceChange(selectedSourceId.value)
  }
}

const loadLLMConfigs = async () => {
  const res = await systemApi.getLLMConfigs()
  llmConfigs.value = (res.data || []).filter((item: LLMConfigOption) => item.is_active === 'Y')
  if (!llmConfigs.value.length) {
    primaryModelConfigId.value = ''
    verifierModelConfigId.value = ''
    return
  }

  const defaultConfig = llmConfigs.value.find(item => item.is_default === 'Y') || llmConfigs.value[0]
  if (!primaryModelConfigId.value || !llmConfigs.value.some(item => item.config_id === primaryModelConfigId.value)) {
    primaryModelConfigId.value = defaultConfig.config_id
  }

  if (
    !verifierModelConfigId.value ||
    verifierModelConfigId.value === primaryModelConfigId.value ||
    !llmConfigs.value.some(item => item.config_id === verifierModelConfigId.value)
  ) {
    verifierModelConfigId.value = llmConfigs.value.find(item => item.config_id !== primaryModelConfigId.value)?.config_id || ''
  }
}

const loadSchemas = async () => {
  if (!selectedSourceId.value) return
  schemaLoading.value = true
  try {
    const res = await sourceApi.getSchemas(selectedSourceId.value)
    schemaOptions.value = res.data?.schemas || []
    if (!selectedSchema.value || !schemaOptions.value.includes(selectedSchema.value)) {
      selectedSchema.value = res.data?.default_schema || schemaOptions.value[0] || ''
    }
  } finally {
    schemaLoading.value = false
  }
}

const loadTables = async () => {
  if (!selectedSourceId.value || !selectedSchema.value) {
    resetSelection()
    return
  }

  tableLoading.value = true
  try {
    const res = await sourceApi.getRemoteTables(selectedSourceId.value, {
      schema: selectedSchema.value,
      prefix: prefixKeyword.value || undefined
    })
    tables.value = res.data?.tables || []
    if (!tables.value.length) {
      selectedTableName.value = ''
      tableDetail.value = null
      columnDrafts.value = []
    }
  } finally {
    tableLoading.value = false
  }
}

const loadTableDetail = async (tableName: string) => {
  if (!selectedSourceId.value || !selectedSchema.value) return
  detailLoading.value = true
  try {
    const res = await sourceApi.getRemoteTableDetail(selectedSourceId.value, tableName, {
      schema: selectedSchema.value,
      sample_limit: 10
    })
    syncDraftsFromDetail(res.data)
  } finally {
    detailLoading.value = false
  }
}

const handleSourceChange = async (sourceId?: string) => {
  if (!sourceId && !selectedSourceId.value) {
    schemaOptions.value = []
    selectedSchema.value = ''
    resetSelection()
    return
  }
  resetSelection()
  await loadSchemas()
  await loadTables()
}

const handleSchemaChange = async () => {
  resetSelection()
  await loadTables()
}

const handleTableFilter = async () => {
  resetSelection()
  await loadTables()
}

const selectTable = async (table: BrowseTable) => {
  selectedTableName.value = table.table_name
  await loadTableDetail(table.table_name)
}

const refreshCurrent = async () => {
  if (!selectedTableName.value) return
  await loadTableDetail(selectedTableName.value)
}

const generateComments = async () => {
  if (!selectedSourceId.value || !selectedSchema.value || !selectedTableName.value) return
  if (!primaryModelConfigId.value) {
    ElMessage.warning('请先选择生成主模型')
    return
  }
  if (!verifierModelConfigId.value) {
    ElMessage.warning('请先选择校验模型，双模型模式至少需要两个启用配置')
    return
  }
  generateLoading.value = true
  try {
    const res = await sourceApi.generateObjectComments(selectedSourceId.value, selectedTableName.value, {
      schema: selectedSchema.value,
      sample_limit: 5,
      primary_model_config_id: primaryModelConfigId.value,
      verifier_model_config_id: verifierModelConfigId.value
    })
    syncDraftsFromDetail(res.data)
    ElMessage.success(generationMode.value === 'fallback' ? '已生成回退规则建议，并完成校验模型复核' : '主模型建议已生成，并完成校验模型复核')
  } finally {
    generateLoading.value = false
  }
}

const saveComments = async () => {
  if (!selectedSourceId.value || !selectedSchema.value || !selectedTableName.value) return
  saveLoading.value = true
  try {
    const res = await sourceApi.saveObjectComments(selectedSourceId.value, selectedTableName.value, {
      schema: selectedSchema.value,
      table_comment: tableCommentDraft.value,
      column_comments: columnDrafts.value.map(item => ({
        column_name: item.column_name,
        comments: item.final_comment || ''
      }))
    })
    generationMode.value = ''
    syncDraftsFromDetail(res.data)
    ElMessage.success('数据库 Comments 已更新')
    await loadTables()
  } finally {
    saveLoading.value = false
  }
}

onMounted(async () => {
  await loadLLMConfigs()
  await loadDataSources()
})

watch(
  () => appStore.currentDomainId,
  async () => {
    selectedSourceId.value = ''
    selectedSchema.value = ''
    prefixKeyword.value = ''
    await loadDataSources()
  }
)
</script>

<style scoped>
.annotation-page {
  height: calc(100vh - 90px);
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.domain-band {
  display: flex;
  align-items: center;
  gap: 10px;
  color: #4b5563;
  font-size: 13px;
}

.domain-label {
  font-weight: 600;
}

.domain-empty {
  color: #9ca3af;
}

.toolbar-band,
.table-panel,
.detail-panel {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
}

.toolbar-band {
  padding: 14px 16px;
}

.toolbar-row {
  display: grid;
  grid-template-columns: minmax(240px, 320px) minmax(180px, 240px) minmax(180px, 240px) auto;
  gap: 12px;
  align-items: center;
}

.toolbar-select,
.toolbar-input {
  width: 100%;
}

.source-option {
  display: flex;
  flex-direction: column;
  line-height: 1.4;
}

.source-option-meta {
  font-size: 12px;
  color: #909399;
}

.content-layout {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  gap: 12px;
}

.table-panel {
  padding: 14px 12px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.panel-head,
.detail-header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
}

.detail-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px;
}

.model-select {
  width: 220px;
}

.panel-title,
.editor-title {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.panel-meta {
  font-size: 12px;
  color: #909399;
}

.table-list {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow-y: auto;
}

.table-item {
  width: 100%;
  border: 1px solid #e5e7eb;
  background: #fff;
  border-radius: 8px;
  padding: 10px 12px;
  text-align: left;
  cursor: pointer;
}

.table-item.active {
  border-color: #1a3a5c;
  background: #eef5ff;
}

.table-item:hover {
  border-color: #409eff;
  background: #f7fbff;
}

.table-item-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.table-name {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
}

.table-comment {
  margin-top: 8px;
  font-size: 12px;
  color: #606266;
  line-height: 1.5;
}

.detail-panel {
  padding: 16px;
  overflow-y: auto;
}

.detail-header h3 {
  margin: 0;
  font-size: 18px;
  color: #1f2937;
}

.status-band {
  margin-top: 12px;
  padding: 10px 12px;
  border-radius: 8px;
  background: #f8fafc;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  color: #475569;
  font-size: 13px;
}

.warning-band {
  background: #fff7ed;
  color: #9a3412;
}

.detail-meta {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 12px;
  color: #606266;
}

.editor-band {
  margin-top: 18px;
}

.editor-title {
  margin-bottom: 10px;
}

@media (max-width: 1200px) {
  .toolbar-row,
  .content-layout {
    grid-template-columns: 1fr;
  }

  .table-panel {
    max-height: 320px;
  }

  .detail-header {
    flex-direction: column;
  }

  .model-select {
    width: 100%;
  }
}
</style>
