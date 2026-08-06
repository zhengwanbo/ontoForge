<template>
  <div class="source-data-page">
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
        <el-button :icon="RefreshRight" :disabled="!selectedSourceId" @click="refreshCurrent">刷新</el-button>
      </div>

      <div class="toolbar-meta" v-if="selectedSource">
        <el-tag size="small" :type="selectedSource.connection_status === 'CONNECTED' ? 'success' : 'info'">
          {{ selectedSource.connection_status === 'CONNECTED' ? '已测试连接' : '未确认连接' }}
        </el-tag>
        <span>{{ formatConnection(selectedSource) }}</span>
        <span v-if="schemaMeta.connected_user">当前连接用户：{{ schemaMeta.connected_user }}</span>
        <span v-if="schemaMeta.default_schema">默认 Schema：{{ schemaMeta.default_schema }}</span>
        <span v-if="tableMeta.totalCount >= 0">表数量：{{ tableMeta.totalCount }}</span>
      </div>

      <el-alert
        v-if="tableError"
        :title="tableError"
        type="warning"
        show-icon
        :closable="false"
        class="toolbar-alert"
      />
    </section>

    <div class="content-layout">
      <aside class="table-panel" v-loading="tableLoading">
        <div class="panel-title">表列表</div>
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
            <div class="table-owner">{{ table.owner }}</div>
            <div class="table-comment">{{ table.comments || '无表描述' }}</div>
          </button>
        </div>
        <el-empty v-else :description="selectedSourceId ? '未找到匹配的表' : currentDomainId ? '请先选择数据库连接' : '请先选择业务分析域'" />
      </aside>

      <main class="detail-panel" v-loading="detailLoading">
        <template v-if="selectedTableName && tableDetail">
          <section class="detail-band">
            <div>
              <h3>{{ tableDetail.table_name }}</h3>
              <div class="detail-meta">
                <span>Schema：{{ tableDetail.owner }}</span>
                <span>字段数：{{ tableDetail.columns.length }}</span>
                <span>样例行数：前 {{ tableDetail.sample_limit }} 行</span>
              </div>
            </div>
            <div class="detail-comment">{{ tableDetail.table_comment || '无表描述' }}</div>
          </section>

          <section class="detail-section">
            <div class="section-title">字段结构</div>
            <el-table :data="tableDetail.columns" border stripe size="small" max-height="360">
              <el-table-column prop="column_id" label="#" width="60" />
              <el-table-column prop="column_name" label="字段名" min-width="180" />
              <el-table-column prop="data_type" label="类型" min-width="160" />
              <el-table-column prop="nullable" label="可空" width="80">
                <template #default="{ row }">
                  <el-tag :type="row.nullable === 'Y' ? 'info' : 'danger'" size="small">{{ row.nullable }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="default_value" label="缺省值" min-width="140" show-overflow-tooltip />
              <el-table-column prop="comments" label="描述" min-width="220" show-overflow-tooltip />
            </el-table>
          </section>

          <section class="detail-section">
            <div class="section-title">前 10 行示例数据</div>
            <el-table
              :data="tableDetail.sample_rows"
              border
              stripe
              size="small"
              max-height="420"
              style="width: 100%"
            >
              <el-table-column
                v-for="column in tableDetail.sample_columns"
                :key="column"
                :prop="column"
                :label="column"
                min-width="160"
                show-overflow-tooltip
              />
            </el-table>
            <el-empty
              v-if="tableDetail.sample_rows.length === 0"
              description="该表暂无样例数据或当前账号无数据访问权限"
            />
          </section>
        </template>

        <el-empty v-else :description="selectedSourceId ? '请从左侧选择表' : currentDomainId ? '请先选择数据库连接' : '请先选择业务分析域'" />
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RefreshRight, Search } from '@element-plus/icons-vue'
import { sourceApi } from '../../api'
import { useAppStore } from '../../stores/app'

interface BrowseDataSource {
  source_id: string
  source_name: string
  source_desc?: string | null
  db_type: string
  schema_name?: string | null
  username: string
  host: string
  port: number
  service_name?: string | null
  sid?: string | null
  is_default: string
  connection_status: string
}

interface BrowseTable {
  owner: string
  table_name: string
  comments?: string | null
  num_rows?: number | null
}

interface TableColumn {
  column_id: number
  column_name: string
  data_type: string
  nullable: string
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

const dataSources = ref<BrowseDataSource[]>([])
const selectedSourceId = ref('')
const schemaOptions = ref<string[]>([])
const selectedSchema = ref('')
const prefixKeyword = ref('')
const tables = ref<BrowseTable[]>([])
const selectedTableName = ref('')
const tableDetail = ref<TableDetail | null>(null)

const schemaMeta = ref({
  connected_user: '',
  default_schema: ''
})
const tableMeta = ref({
  totalCount: 0
})

const schemaLoading = ref(false)
const tableLoading = ref(false)
const detailLoading = ref(false)
const tableError = ref('')
const appStore = useAppStore()

const selectedSource = computed(() => dataSources.value.find(item => item.source_id === selectedSourceId.value) || null)
const currentDomainId = computed(() => appStore.currentDomainId)
const currentDomainName = computed(() => appStore.currentDomainName)

const formatConnection = (source: BrowseDataSource) => {
  const target = source.service_name ? `/${source.service_name}` : source.sid ? `:${source.sid}` : ''
  return `${source.host}:${source.port}${target} (${source.username})`
}

const resetTableView = () => {
  tables.value = []
  selectedTableName.value = ''
  tableDetail.value = null
  tableMeta.value.totalCount = 0
  tableError.value = ''
}

const loadDataSources = async () => {
  if (!currentDomainId.value) {
    dataSources.value = []
    selectedSourceId.value = ''
    schemaOptions.value = []
    selectedSchema.value = ''
    resetTableView()
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

const loadSchemas = async () => {
  if (!selectedSourceId.value) return
  schemaLoading.value = true
  try {
    const res = await sourceApi.getSchemas(selectedSourceId.value)
    schemaOptions.value = res.data?.schemas || []
    schemaMeta.value.connected_user = res.data?.connected_user || ''
    schemaMeta.value.default_schema = res.data?.default_schema || ''
    if (!selectedSchema.value || !schemaOptions.value.includes(selectedSchema.value)) {
      selectedSchema.value = schemaMeta.value.default_schema || schemaOptions.value[0] || ''
    }
  } finally {
    schemaLoading.value = false
  }
}

const loadTables = async () => {
  if (!selectedSourceId.value || !selectedSchema.value) {
    resetTableView()
    return
  }

  tableLoading.value = true
  tableError.value = ''
  try {
    const res = await sourceApi.getRemoteTables(selectedSourceId.value, {
      schema: selectedSchema.value,
      prefix: prefixKeyword.value || undefined
    })
    tables.value = res.data?.tables || []
    tableMeta.value.totalCount = tables.value.length

    if (!tables.value.length) {
      selectedTableName.value = ''
      tableDetail.value = null
      return
    }

    const current = tables.value.find(item => item.table_name === selectedTableName.value)
    const nextTable = current || tables.value[0]
    await selectTable(nextTable)
  } catch (error: any) {
    resetTableView()
    tableError.value = error?.response?.data?.detail || error?.message || '获取数据库表列表失败'
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
    tableDetail.value = res.data || null
  } catch {
    tableDetail.value = null
  } finally {
    detailLoading.value = false
  }
}

const handleSourceChange = async (sourceId?: string) => {
  if (!sourceId && !selectedSourceId.value) {
    schemaOptions.value = []
    selectedSchema.value = ''
    prefixKeyword.value = ''
    resetTableView()
    return
  }

  prefixKeyword.value = ''
  resetTableView()
  await loadSchemas()
  await loadTables()
}

const handleSchemaChange = async () => {
  resetTableView()
  await loadTables()
}

const handleTableFilter = async () => {
  resetTableView()
  await loadTables()
}

const selectTable = async (table: BrowseTable) => {
  selectedTableName.value = table.table_name
  await loadTableDetail(table.table_name)
}

const refreshCurrent = async () => {
  await loadSchemas()
  await loadTables()
}

onMounted(async () => {
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
.source-data-page {
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

.toolbar-band {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 14px 16px;
}

.toolbar-row {
  display: grid;
  grid-template-columns: minmax(240px, 320px) minmax(180px, 240px) minmax(180px, 240px) auto auto;
  gap: 12px;
  align-items: center;
}

.toolbar-select,
.toolbar-input {
  width: 100%;
}

.toolbar-meta {
  margin-top: 10px;
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  font-size: 12px;
  color: #606266;
}

.toolbar-alert {
  margin-top: 12px;
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

.table-panel,
.detail-panel {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  min-height: 0;
}

.table-panel {
  padding: 14px 12px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.panel-title,
.section-title {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
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

.table-item:hover {
  border-color: #409eff;
  background: #f7fbff;
}

.table-item.active {
  border-color: #1a3a5c;
  background: #eef5ff;
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

.table-owner {
  margin-top: 6px;
  font-size: 12px;
  color: #909399;
}

.table-comment {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.5;
  color: #606266;
}

.detail-panel {
  padding: 16px;
  overflow-y: auto;
}

.detail-band {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 14px;
  border-bottom: 1px solid #ebeef5;
}

.detail-band h3 {
  margin: 0;
  font-size: 18px;
  color: #1f2937;
}

.detail-meta {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 12px;
  color: #606266;
}

.detail-comment {
  max-width: 420px;
  font-size: 13px;
  line-height: 1.6;
  color: #606266;
}

.detail-section {
  margin-top: 18px;
}

.detail-section .section-title {
  margin-bottom: 10px;
}

@media (max-width: 1200px) {
  .toolbar-row {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .content-layout {
    grid-template-columns: 1fr;
  }

  .table-panel {
    max-height: 320px;
  }

  .detail-band {
    flex-direction: column;
  }
}
</style>
