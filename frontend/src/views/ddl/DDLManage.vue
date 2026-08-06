<template>
  <div class="ddl-page">
    <div class="top-bar">
      <el-select v-model="currentDomainId" placeholder="选择业务分析域" @change="handleDomainChange" style="width: 220px">
        <el-option v-for="d in domains" :key="d.domain_id" :label="d.domain_name" :value="d.domain_id" />
      </el-select>
      <div class="actions">
        <el-button @click="reloadDomainContext" :loading="contextLoading">刷新本体</el-button>
        <el-button type="primary" @click="generateDDL" :loading="generateLoading">生成DDL</el-button>
        <el-button type="success" @click="showExecuteDialog" :disabled="!ddlContent">执行DDL</el-button>
        <el-button @click="showLogsDialog">DDL历史</el-button>
      </div>
    </div>

    <div class="context-grid">
      <el-card class="context-card" shadow="never">
        <template #header>
          <div class="card-header">
            <span>当前本体对象与属性</span>
            <el-tag size="small" type="info">实体 {{ ontologyEntities.length }}</el-tag>
          </div>
        </template>
        <div v-if="contextLoading" class="state-box">正在加载当前本体上下文…</div>
        <el-empty v-else-if="!currentDomainId" description="请先选择业务分析域" />
        <el-empty v-else-if="!ontologyEntities.length" description="当前分析域还没有本体对象" />
        <el-collapse v-else>
          <el-collapse-item
            v-for="entity in ontologyEntities"
            :key="entity.entity_id"
            :name="entity.entity_id"
            :title="`${entity.entity_display_name || entity.entity_name} | ${entity.build_type} | 属性 ${entity.properties?.length || 0}`"
          >
            <div class="entity-summary">
              <div>实体名：{{ entity.entity_name }}</div>
              <div>表名：{{ entity.table_name || '-' }}</div>
              <div>状态：{{ entity.status || '-' }}</div>
              <div>说明：{{ entity.entity_desc || '无' }}</div>
            </div>
            <el-table :data="entity.properties || []" border stripe size="small" max-height="240">
              <el-table-column prop="property_name" label="属性名" min-width="160" />
              <el-table-column prop="property_display_name" label="显示名" min-width="140" />
              <el-table-column prop="data_type" label="类型" width="120" />
              <el-table-column label="主键" width="70">
                <template #default="{ row }">{{ row.is_primary_key === 'Y' ? 'Y' : 'N' }}</template>
              </el-table-column>
              <el-table-column label="可空" width="70">
                <template #default="{ row }">{{ row.is_nullable === 'Y' ? 'Y' : 'N' }}</template>
              </el-table-column>
              <el-table-column prop="property_desc" label="说明" min-width="200" show-overflow-tooltip />
            </el-table>
          </el-collapse-item>
        </el-collapse>
      </el-card>

      <el-card class="context-card" shadow="never">
        <template #header>
          <div class="card-header">
            <span>当前关系</span>
            <el-tag size="small" type="warning">关系 {{ ontologyRelations.length }}</el-tag>
          </div>
        </template>
        <div v-if="contextLoading" class="state-box">正在加载当前关系上下文…</div>
        <el-empty v-else-if="!currentDomainId" description="请先选择业务分析域" />
        <el-empty v-else-if="!ontologyRelations.length" description="当前分析域还没有关系" />
        <el-table v-else :data="ontologyRelations" border stripe size="small" max-height="520">
          <el-table-column prop="relation_name" label="关系名" min-width="160" />
          <el-table-column label="源实体" min-width="140">
            <template #default="{ row }">{{ entityNameMap[row.source_entity_id] || row.source_entity_id }}</template>
          </el-table-column>
          <el-table-column label="目标实体" min-width="140">
            <template #default="{ row }">{{ entityNameMap[row.target_entity_id] || row.target_entity_id }}</template>
          </el-table-column>
          <el-table-column prop="relation_type" label="类型" width="130" />
          <el-table-column prop="relation_table_name" label="关系表/边表" min-width="180" show-overflow-tooltip />
          <el-table-column prop="relation_desc" label="说明" min-width="220" show-overflow-tooltip />
        </el-table>
      </el-card>
    </div>

    <el-card v-if="latestBlueprint" class="context-card" shadow="never" style="margin-bottom: 12px;">
      <template #header>
        <div class="card-header">
          <span>最新 Guide 设计包上下文</span>
          <el-tag size="small" type="info">v{{ latestBlueprint.blueprint_version || '-' }}</el-tag>
        </div>
      </template>
      <div class="ddl-blueprint-grid">
        <div class="ddl-blueprint-item"><span>规则摘要</span><strong>{{ latestBlueprint.rule_summary?.rule_type || '-' }}</strong></div>
        <div class="ddl-blueprint-item"><span>规则表</span><strong>{{ latestBlueprint.rule_summary?.rule_table_name || '-' }}</strong></div>
        <div class="ddl-blueprint-item"><span>实体候选</span><strong>{{ (latestBlueprint.entity_candidates || []).length }}</strong></div>
        <div class="ddl-blueprint-item"><span>关系候选</span><strong>{{ (latestBlueprint.relation_candidates || []).length }}</strong></div>
      </div>
      <div class="ddl-blueprint-note">{{ latestBlueprint.rule_summary?.summary || '当前没有业务规则摘要。' }}</div>
    </el-card>

    <el-alert
      v-if="currentDomainId"
      type="info"
      :closable="false"
      show-icon
      style="margin-bottom: 12px"
    >
      <template #title>
        当前 DDL 生成遵循“源数据驱动”原则：实体使用已确认的属性映射或 `entity_mapping.view_sql`；关系由两端本体节点表的唯一主键生成 `EDGE_ID / SOURCE_ID / TARGET_ID` 边表，并优先采用已保存的 Join 条件。
      </template>
    </el-alert>

    <div v-if="ddlContent" class="ddl-content">
      <div class="ddl-stats">
        <el-descriptions :column="4" border size="small">
          <el-descriptions-item label="实体数">{{ ddlStats.entityCount }}</el-descriptions-item>
          <el-descriptions-item label="关系数">{{ ddlStats.relationCount }}</el-descriptions-item>
          <el-descriptions-item label="DDL语句数">{{ ddlStatements.length }}</el-descriptions-item>
          <el-descriptions-item label="设计包版本">v{{ ddlStats.blueprintVersion || '-' }}</el-descriptions-item>
        </el-descriptions>
      </div>

      <div class="ddl-statements">
        <div v-for="(stmt, idx) in ddlStatements" :key="idx" class="ddl-statement-item">
          <div class="statement-header">
            <el-tag :type="getStmtTagType(stmt.type)" size="small">{{ stmt.type }}</el-tag>
            <span class="statement-name">{{ stmt.name }}</span>
          </div>
          <pre class="statement-content">{{ stmt.sql }}</pre>
        </div>
      </div>

      <el-card shadow="never">
        <template #header>
          <div class="card-header">
            <span>DDL完整脚本（可编辑）</span>
            <span class="editor-hint">可在执行前直接修改脚本内容</span>
          </div>
        </template>
        <el-input
          v-model="ddlContent"
          type="textarea"
          :rows="22"
          resize="vertical"
          style="font-family: monospace; font-size: 13px;"
        />
      </el-card>
    </div>

    <el-empty v-if="!ddlContent && !generateLoading && currentDomainId && !contextLoading" description="当前已显示本体对象和关系信息，确认无误后点击生成DDL" />

    <el-dialog v-model="executeDialogVisible" title="执行DDL" width="500px">
      <el-alert type="warning" :closable="false" show-icon style="margin-bottom: 16px">
        <template #title>⚠️ DDL 将在下方选择的目标对象数据库中执行，平台元数据库仅保存日志和状态，不会创建业务对象。</template>
      </el-alert>
      <el-form :model="executeForm" label-width="100px">
        <el-form-item label="目标对象库" required>
          <el-select v-model="executeForm.target_source_id" placeholder="选择执行 DDL 的 Oracle 数据源" filterable style="width: 100%">
            <el-option
              v-for="source in targetDataSources"
              :key="source.source_id"
              :label="`${source.source_name} / ${source.schema_name || source.username}`"
              :value="source.source_id"
            >
              <span>{{ source.source_name }}</span>
              <span style="float: right; color: #8492a6; font-size: 12px">{{ source.host }}:{{ source.port }} / {{ source.schema_name || source.username }}</span>
            </el-option>
          </el-select>
          <div class="target-source-hint">请选择存放本体对象表、边表和 Property Graph 的业务 Oracle 数据库。</div>
        </el-form-item>
        <el-form-item label="执行模式">
          <el-radio-group v-model="executeForm.execute_mode">
            <el-radio value="all">全部执行（先清理旧对象）</el-radio>
            <el-radio value="step_by_step">逐条执行</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="跳过已有">
          <el-switch v-model="executeForm.skip_existing" />
          <span class="skip-existing-hint">通常保持关闭：生成脚本已包含删除旧本体对象的语句。</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="executeDialogVisible = false">取消</el-button>
        <el-button type="danger" @click="executeDDL" :loading="executeLoading">确认执行</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="executionResultVisible" title="DDL 执行结果" width="1080px" top="5vh">
      <el-alert
        v-if="executionResult"
        :type="executionResult.failed ? 'error' : 'success'"
        :closable="false"
        show-icon
        style="margin-bottom: 14px"
      >
        <template #title>共 {{ executionResult.total || 0 }} 条：成功 {{ executionResult.success || 0 }}，失败 {{ executionResult.failed || 0 }}，跳过 {{ executionResult.skipped || 0 }}</template>
      </el-alert>
      <el-table :data="executionResult?.details || []" border stripe size="small" max-height="560">
        <el-table-column type="expand">
          <template #default="{ row }"><pre class="execution-sql">{{ row.statement }};</pre></template>
        </el-table-column>
        <el-table-column prop="object_type" label="对象类型" width="130" />
        <el-table-column prop="object_name" label="对象名称" min-width="180" />
        <el-table-column label="执行结果" width="100">
          <template #default="{ row }"><el-tag :type="executionStatusType(row.status)" size="small">{{ executionStatusLabel(row.status) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="说明 / 错误" min-width="300">
          <template #default="{ row }">{{ row.error || row.message || (row.status === 'success' ? '执行成功' : '-') }}</template>
        </el-table-column>
      </el-table>
      <template #footer><el-button type="primary" @click="executionResultVisible = false">关闭</el-button></template>
    </el-dialog>

    <el-dialog v-model="logsDialogVisible" title="DDL生成与执行历史" width="920px">
      <el-table :data="ddlLogs" border stripe size="small">
        <el-table-column prop="executed_at" label="执行时间" width="180" />
        <el-table-column prop="execution_result" label="结果" width="80">
          <template #default="{ row }">
            <el-tag :type="ddlHistoryStatusType(row.execution_result)" size="small">{{ ddlHistoryStatusLabel(row.execution_result) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="executed_by" label="执行人" width="100" />
        <el-table-column prop="execution_duration" label="耗时(秒)" width="80" />
        <el-table-column prop="error_message" label="错误信息" min-width="200" />
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="viewHistorySql(row)">查看 SQL</el-button>
            <el-button link :disabled="!row.ddl_content" @click="loadHistorySql(row)">载入</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <el-dialog v-model="historySqlVisible" :title="historySqlTitle" width="900px" top="6vh">
      <el-input :model-value="selectedHistorySql?.ddl_content || ''" type="textarea" :rows="26" readonly style="font-family: monospace; font-size: 13px;" />
      <template #footer>
        <el-button @click="historySqlVisible = false">关闭</el-button>
        <el-button type="primary" :disabled="!selectedHistorySql?.ddl_content" @click="loadHistorySql(selectedHistorySql)">载入当前编辑器</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { domainApi, ddlApi, sourceApi } from '../../api'
import { useAppStore } from '../../stores/app'

const appStore = useAppStore()
const currentDomainId = ref(appStore.currentDomainId || '')
const domains = ref<any[]>([])
const ontologyEntities = ref<any[]>([])
const ontologyRelations = ref<any[]>([])
const latestBlueprint = ref<any>(null)
const ddlContent = ref('')
const ddlStatements = ref<any[]>([])
const ddlStats = ref<any>({ entityCount: 0, relationCount: 0, blueprintVersion: '' })
const ddlLogs = ref<any[]>([])
const targetDataSources = ref<any[]>([])
const contextLoading = ref(false)
const generateLoading = ref(false)
const executeLoading = ref(false)
const executeDialogVisible = ref(false)
const executionResultVisible = ref(false)
const executionResult = ref<any>(null)
const logsDialogVisible = ref(false)
const historySqlVisible = ref(false)
const selectedHistorySql = ref<any>(null)
const executeForm = ref({ target_source_id: '', execute_mode: 'all', skip_existing: false })

const entityNameMap = computed<Record<string, string>>(() => {
  const mapping: Record<string, string> = {}
  ontologyEntities.value.forEach(entity => {
    mapping[entity.entity_id] = entity.entity_display_name || entity.entity_name || entity.entity_id
  })
  return mapping
})

const hasEntitySourceGap = computed(() => {
  return ontologyEntities.value.some((entity: any) => {
    const properties = entity.properties || []
    const hasEntitySql = Boolean(entity.entity_mapping?.view_sql)
    const hasMappedProperty = properties.some((prop: any) =>
      prop?.mapping && (
        ((prop.mapping.mapping_type || '').toUpperCase() === 'DIRECT' && prop.mapping.source_table && prop.mapping.source_column) ||
        ((prop.mapping.mapping_type || '').toUpperCase() === 'COMPUTED' && prop.mapping.source_table && prop.mapping.formula_expr)
      )
    )
    return !hasEntitySql && !hasMappedProperty
  })
})

const getStmtTagType = (type: string) => {
  const map: Record<string, string> = {
    create_table: '',
    create_view: 'success',
    create_graph: 'warning',
    drop_table: 'danger',
    drop_view: 'danger',
    drop_graph: 'danger',
    comment_table: 'info',
    comment_column: 'info'
  }
  return map[type] || 'info'
}

const ddlHistoryStatusLabel = (status: string) => ({
  GENERATED: '已生成',
  SUCCESS: '执行成功',
  FAILED: '执行失败'
}[status] || status)

const ddlHistoryStatusType = (status: string) => ({
  GENERATED: 'info',
  SUCCESS: 'success',
  FAILED: 'danger'
}[status] || 'info')

const executionStatusLabel = (status: string) => ({ success: '成功', failed: '失败', skipped: '已跳过' }[status] || status)
const executionStatusType = (status: string) => ({ success: 'success', failed: 'danger', skipped: 'warning' }[status] || 'info')

const historySqlTitle = computed(() => {
  if (!selectedHistorySql.value) return 'DDL SQL'
  return `${ddlHistoryStatusLabel(selectedHistorySql.value.execution_result)} DDL SQL · ${selectedHistorySql.value.executed_at || ''}`
})

const loadDomains = async () => {
  try {
    const res = await domainApi.list('ACTIVE')
    domains.value = res.data || []
  } catch (e) {}
}

const reloadDomainContext = async () => {
  if (!currentDomainId.value) {
    ontologyEntities.value = []
    ontologyRelations.value = []
    latestBlueprint.value = null
    return
  }
  contextLoading.value = true
  try {
    const res = await ddlApi.getContext(currentDomainId.value)
    ontologyEntities.value = res.data?.entities || []
    ontologyRelations.value = res.data?.relations || []
    latestBlueprint.value = res.data?.blueprint || null
  } catch (e) {
    ontologyEntities.value = []
    ontologyRelations.value = []
    latestBlueprint.value = null
  } finally {
    contextLoading.value = false
  }
}

const loadLogs = async () => {
  try {
    const res = await ddlApi.getLogs(currentDomainId.value)
    ddlLogs.value = res.data || []
  } catch (e) {}
}

const loadTargetDataSources = async () => {
  if (!currentDomainId.value) {
    targetDataSources.value = []
    executeForm.value.target_source_id = ''
    return
  }
  try {
    const res = await sourceApi.listDataSources(currentDomainId.value)
    targetDataSources.value = (res.data || []).filter((source: any) => (source.db_type || '').toLowerCase() === 'oracle')
    if (!targetDataSources.value.some(source => source.source_id === executeForm.value.target_source_id)) {
      executeForm.value.target_source_id = targetDataSources.value.find(source => source.is_default === 'Y')?.source_id || ''
    }
  } catch (e) {
    targetDataSources.value = []
  }
}

const handleDomainChange = async () => {
  ddlContent.value = ''
  ddlStatements.value = []
  ddlStats.value = { entityCount: 0, relationCount: 0, blueprintVersion: '' }
  executeForm.value.target_source_id = ''
  await Promise.all([reloadDomainContext(), loadTargetDataSources()])
}

const generateDDL = async () => {
  if (!currentDomainId.value) {
    ElMessage.warning('请先选择业务分析域')
    return
  }
  if (!ontologyEntities.value.length) {
    ElMessage.warning('当前分析域下没有本体对象，无法生成DDL')
    return
  }
  if (hasEntitySourceGap.value) {
    ElMessage.warning('存在未配置源数据映射的实体，请先在数据映射中确认属性映射或维护实体级 view_sql')
    return
  }
  generateLoading.value = true
  try {
    const res = await ddlApi.generate(currentDomainId.value)
    ddlContent.value = res.data?.full_ddl || ''
    ddlStatements.value = res.data?.ddl_statements || []
    ddlStats.value = {
      entityCount: res.data?.entity_count || 0,
      relationCount: res.data?.relation_count || 0,
      blueprintVersion: res.data?.blueprint_version || ''
    }
    if (!ddlStatements.value.length || !ddlContent.value.trim()) {
      ElMessage.warning('当前未生成有效DDL，请检查本体、映射或部署设计配置')
      return
    }
    ElMessage.success('DDL生成完成')
  } catch (e: any) {
    ElMessage.error(e?.message || 'DDL生成失败')
  } finally {
    generateLoading.value = false
  }
}

const showExecuteDialog = () => {
  if (!targetDataSources.value.length) {
    ElMessage.warning('当前分析域没有可用的 Oracle 目标对象数据库，请先在数据源管理中配置并启用')
    return
  }
  executeDialogVisible.value = true
}

const showLogsDialog = () => {
  logsDialogVisible.value = true
  loadLogs()
}

const viewHistorySql = (row: any) => {
  selectedHistorySql.value = row
  historySqlVisible.value = true
}

const loadHistorySql = (row: any) => {
  if (!row?.ddl_content) return
  ddlContent.value = row.ddl_content
  ddlStatements.value = []
  ddlStats.value = { entityCount: ontologyEntities.value.length, relationCount: ontologyRelations.value.length, blueprintVersion: latestBlueprint.value?.blueprint_version || '' }
  historySqlVisible.value = false
  logsDialogVisible.value = false
  ElMessage.success('已载入历史 DDL SQL，可继续查看、编辑或执行')
}

const executeDDL = async () => {
  if (!executeForm.value.target_source_id) {
    ElMessage.warning('请选择目标对象数据库')
    return
  }
  executeLoading.value = true
  try {
    const res: any = await ddlApi.execute(currentDomainId.value, {
      ddl_content: ddlContent.value,
      target_source_id: executeForm.value.target_source_id,
      execute_mode: executeForm.value.execute_mode,
      skip_existing: executeForm.value.skip_existing
    })
    const result = res.data?.result || {}
    executionResult.value = result
    executionResultVisible.value = true
    if (result.failed) ElMessage.warning(`DDL执行完成，但有 ${result.failed} 条失败，请查看逐语句结果`)
    else ElMessage.success(`DDL执行完成: 成功${result.success || 0}条, 跳过${result.skipped || 0}条`)
    executeDialogVisible.value = false
    await loadLogs()
    await reloadDomainContext()
  } catch (e: any) {
    ElMessage.error(e?.message || 'DDL执行失败')
  } finally {
    executeLoading.value = false
  }
}

onMounted(async () => {
  await loadDomains()
  if (currentDomainId.value) {
    await Promise.all([reloadDomainContext(), loadTargetDataSources()])
  }
})
</script>

<style scoped>
.ddl-page {
  height: calc(100vh - 90px);
  overflow-y: auto;
}

.top-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 0;
  gap: 12px;
  flex-wrap: wrap;
}

.actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.context-grid {
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: 16px;
  margin-top: 10px;
}

.context-card,
.ddl-content {
  min-width: 0;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.editor-hint {
  font-size: 12px;
  color: #6b7280;
}

.target-source-hint {
  margin-top: 6px;
  color: #909399;
  font-size: 12px;
  line-height: 1.45;
}

.skip-existing-hint {
  margin-left: 10px;
  color: #909399;
  font-size: 12px;
}

.execution-sql {
  max-height: 220px;
  margin: 4px 0;
  padding: 12px;
  overflow: auto;
  border-radius: 6px;
  background: #f5f7fa;
  color: #334155;
  font-family: 'SFMono-Regular', Consolas, monospace;
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
}

.state-box {
  padding: 18px 0;
  color: #6b7280;
  font-size: 13px;
}

.entity-summary {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 16px;
  margin-bottom: 12px;
  font-size: 13px;
  color: #4b5563;
}

.ddl-blueprint-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 10px;
}

.ddl-blueprint-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  background: #f8fbff;
  border: 1px solid #e3edf7;
  border-radius: 8px;
  font-size: 12px;
  color: #5b6f87;
}

.ddl-blueprint-item strong {
  font-size: 14px;
  color: #1a3a5c;
}

.ddl-blueprint-note {
  font-size: 12px;
  color: #5f748c;
  line-height: 1.7;
}

.ddl-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
  margin-top: 16px;
}

.ddl-stats {
  margin-top: 4px;
}

.ddl-statements {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.ddl-statement-item {
  background: #fff;
  border: 1px solid #e8e8e8;
  border-radius: 6px;
  padding: 12px;
}

.statement-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.statement-name {
  font-weight: 500;
  color: #333;
}

.statement-content {
  background: #f5f7fa;
  padding: 10px;
  font-size: 13px;
  font-family: 'Courier New', monospace;
  overflow-x: auto;
  white-space: pre-wrap;
  border-radius: 4px;
}

@media (max-width: 1100px) {
  .context-grid {
    grid-template-columns: 1fr;
  }

  .entity-summary {
    grid-template-columns: 1fr;
  }

  .ddl-blueprint-grid {
    grid-template-columns: 1fr 1fr;
  }
}
</style>
