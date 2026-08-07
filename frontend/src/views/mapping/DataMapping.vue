<template>
  <div class="mapping-page">
    <div class="top-bar">
      <el-select v-model="currentDomainId" placeholder="选择业务分析域" @change="handleDomainChange" class="toolbar-select">
        <el-option v-for="d in domains" :key="d.domain_id" :label="d.domain_name" :value="d.domain_id" />
      </el-select>
      <el-select v-model="currentEntityId" placeholder="选择实体" @change="handleEntityChange" class="toolbar-select" :disabled="!currentDomainId">
        <el-option v-for="e in entities" :key="e.entity_id" :label="e.entity_display_name || e.entity_name" :value="e.entity_id" />
      </el-select>
      <el-select v-model="selectedSourceId" placeholder="选择数据库连接" @change="handleSourceChange" class="toolbar-select">
        <el-option
          v-for="source in dataSources"
          :key="source.source_id"
          :label="source.source_name"
          :value="source.source_id"
        />
      </el-select>
      <el-select
        v-model="selectedSchema"
        placeholder="选择 Schema"
        @change="handleSchemaChange"
        class="toolbar-select"
        :disabled="!selectedSourceId || schemaLoading"
      >
        <el-option v-for="schema in schemaOptions" :key="schema" :label="schema" :value="schema" />
      </el-select>
    </div>

    <div v-if="currentEntityId" class="mapping-content">
      <el-alert
        v-if="manualReviewPropertyCount || manualReviewRelationCount"
        class="manual-review-alert"
        type="warning"
        :closable="false"
        show-icon
      >
        <template #title>
          当前实体有 {{ manualReviewPropertyCount }} 个属性、{{ manualReviewRelationCount }} 条关系需要人工确认
        </template>
        <div>请优先补齐缺失来源、核对低置信度建议，并确认关系 Join 或关系表字段后再生成 DDL。</div>
      </el-alert>
      <el-card class="mapping-graph-card">
        <template #header>
          <span>映射结果预览</span>
        </template>
        <div v-if="mappingNodes.length > 0 || currentEntityMappedRows.length > 0" class="mapping-preview">
          <div class="mapping-preview-summary">
            <div class="summary-item">
              <div class="summary-label">实体总数</div>
              <div class="summary-value">{{ mappingNodes.length }}</div>
            </div>
            <div class="summary-item">
              <div class="summary-label">已开始映射实体</div>
              <div class="summary-value">{{ mappedEntityCount }}</div>
            </div>
            <div class="summary-item">
              <div class="summary-label">关系总数</div>
              <div class="summary-value">{{ mappingEdges.length }}</div>
            </div>
          </div>

          <div class="mapping-preview-layout">
            <div class="preview-panel">
              <div class="preview-title">业务域实体映射概览</div>
              <div class="preview-subtitle">当前实体会高亮显示，其余实体展示整体映射进度。</div>
              <div class="entity-overview-list">
                <div
                  v-for="node in mappingNodes"
                  :key="node.id"
                  class="entity-overview-card"
                  :class="{ active: node.id === currentEntityId }"
                  @click="switchCurrentEntity(node.id)"
                >
                  <div class="entity-overview-header">
                    <div class="entity-overview-name">{{ node.displayName || node.name }}</div>
                    <el-tag size="small" :type="node.buildType === 'VIEW' ? 'info' : 'success'">
                      {{ node.buildType || 'TABLE' }}
                    </el-tag>
                  </div>
                  <div class="entity-overview-meta">
                    <span>{{ formatEntityStatus(node.status) }}</span>
                    <span>{{ node.mappedCount }} / {{ node.propertiesCount }} 属性</span>
                  </div>
                  <el-progress
                    :percentage="getNodeProgress(node)"
                    :stroke-width="8"
                    :status="getNodeProgress(node) === 100 ? 'success' : undefined"
                  />
                </div>
              </div>
            </div>

            <div class="preview-panel">
              <div class="preview-title">当前实体属性与映射明细</div>
              <div class="preview-subtitle">
                {{ currentEntityLabel }} 共 {{ currentEntityRows.length }} 个属性，已映射 {{ currentEntityMappedRows.length }} 个，未映射 {{ currentEntityPendingCount }} 个。
              </div>

              <div v-if="currentEntityRows.length > 0" class="mapped-property-list">
                <div v-for="row in currentEntityRows" :key="row.property_id || row.property_name" class="mapped-property-item">
                  <div class="mapped-property-main">
                    <span class="mapped-property-name">{{ row.property_display_name || row.property_name }}</span>
                    <div class="mapped-property-tags">
                      <el-tag size="small" :type="row.source_table && row.source_column ? 'success' : 'info'">
                        {{ row.source_table && row.source_column ? '已映射' : '未映射' }}
                      </el-tag>
                      <el-tag size="small" effect="plain">{{ row.mapping_type || 'DIRECT' }}</el-tag>
                    </div>
                  </div>
                  <div class="mapped-property-source">
                    {{ row.source_table && row.source_column ? `${row.source_table}.${row.source_column}` : '未提供来源字段' }}
                  </div>
                  <div v-if="row.formula_desc" class="mapped-property-reason">
                    {{ row.formula_desc }}
                  </div>
                </div>
              </div>
              <el-empty v-else description="当前实体暂无属性信息" />
            </div>
          </div>

          <div class="preview-panel relation-panel">
            <div class="preview-title">当前实体关系摘要</div>
            <div class="preview-subtitle">展示当前实体在业务域关系中的上下游位置。</div>
            <div v-if="relatedEdges.length > 0" class="relation-list">
              <div v-for="edge in relatedEdges" :key="edge.id" class="relation-item">
                <span class="relation-entity">{{ getEntityName(edge.source) }}</span>
                <span class="relation-arrow">→</span>
                <span class="relation-entity">{{ getEntityName(edge.target) }}</span>
                <span class="relation-name">{{ edge.name || edge.type || '未命名关系' }}</span>
              </div>
            </div>
            <el-empty v-else description="当前实体尚未建立关系" />
          </div>
        </div>
        <el-empty v-else description="当前业务域暂无可预览的映射结果" />
      </el-card>

      <el-card class="property-mapping-card">
        <template #header>
          <div class="card-header">
            <span>属性映射配置</span>
            <div class="header-actions">
              <el-checkbox v-model="showOnlyManualReview">仅显示待人工确认（{{ manualReviewPropertyCount }}）</el-checkbox>
              <el-button size="small" @click="addManualProperty">
                新增属性
              </el-button>
              <el-button type="primary" size="small" :loading="saveLoading" :disabled="!hasUnsavedChanges" @click="saveManualMappings">
                保存人工映射
              </el-button>
            </div>
          </div>
        </template>

        <div class="mapping-progress">
          <el-progress :percentage="mappingProgress" :stroke-width="10" />
          <span>已准备映射 {{ mappedCount }} / {{ totalPropertyCount }} 个属性</span>
        </div>

        <el-table :data="visibleMappingRows" :row-class-name="mappingRowClassName" row-key="local_id" border stripe size="small">
          <el-table-column label="属性来源" width="110">
            <template #default="{ row }">
              <el-tag size="small" :type="row.property_id ? 'success' : 'warning'">
                {{ row.property_id ? '已有属性' : '待新增' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="property_name" label="本体属性" width="170">
            <template #default="{ row }">
              <el-input v-model="row.property_name" size="small" placeholder="属性名" />
            </template>
          </el-table-column>
          <el-table-column prop="property_display_name" label="显示名称" width="150">
            <template #default="{ row }">
              <el-input v-model="row.property_display_name" size="small" placeholder="显示名称" />
            </template>
          </el-table-column>
          <el-table-column prop="data_type" label="数据类型" width="130">
            <template #default="{ row }">
              <el-input v-model="row.data_type" size="small" placeholder="数据类型" />
            </template>
          </el-table-column>
          <el-table-column prop="source_table" label="源表" width="180">
            <template #default="{ row }">
              <el-select
                v-model="row.source_table"
                size="small"
                placeholder="选择源表"
                filterable
                @change="handleSourceTableChange(row)"
              >
                <el-option
                  v-for="t in sourceTables"
                  :key="t.table_name"
                  :label="t.comments ? `${t.table_name} (${t.comments})` : t.table_name"
                  :value="t.table_name"
                />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column prop="source_column" label="源字段" width="200">
            <template #default="{ row }">
              <el-select
                v-model="row.source_column"
                size="small"
                placeholder="选择字段"
                filterable
                @visible-change="(visible: boolean) => visible && preloadSourceColumns(row.source_table)"
                @change="handleSourceColumnChange(row)"
              >
                <el-option
                  v-for="c in getSourceColumns(row.source_table)"
                  :key="c.column_name"
                  :label="`${c.column_name} (${c.data_type})`"
                  :value="c.column_name"
                />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column prop="mapping_type" label="映射类型" width="120">
            <template #default="{ row }">
              <el-select v-model="row.mapping_type" size="small">
                <el-option label="直接映射" value="DIRECT" />
                <el-option label="计算映射" value="COMPUTED" />
                <el-option label="常量映射" value="CONSTANT" />
                <el-option label="LLM推导" value="LLM_DERIVED" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column prop="formula_expr" label="计算公式" width="180">
            <template #default="{ row }">
              <el-input v-if="row.mapping_type === 'COMPUTED'" v-model="row.formula_expr" size="small" placeholder="SQL表达式" />
              <span v-else>{{ row.formula_expr || '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="confidence" label="置信度" width="90">
            <template #default="{ row }">
              <el-tag v-if="row.confidence" :type="confidenceTagType(row.confidence)" size="small">
                {{ row.confidence }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="mapping_status" label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="mappingStatusTagType(row.mapping_status)" size="small">
                {{ mappingStatusLabel(row.mapping_status) }}
              </el-tag>
              <div v-if="needsPropertyReview(row)" class="manual-review-hint">{{ propertyReviewReason(row) }}</div>
            </template>
          </el-table-column>
          <el-table-column prop="property_desc" label="属性说明" min-width="180">
            <template #default="{ row }">
              <el-input v-model="row.property_desc" size="small" placeholder="属性说明" />
            </template>
          </el-table-column>
          <el-table-column prop="formula_desc" label="匹配理由" min-width="220">
            <template #default="{ row }">
              <el-input v-model="row.formula_desc" size="small" placeholder="人工填写匹配理由" />
            </template>
          </el-table-column>
          <el-table-column label="操作" width="160" fixed="right">
            <template #default="{ row }">
              <div class="row-actions">
                <el-button link type="primary" @click="clearRowMapping(row)">清空映射</el-button>
                <el-button link type="danger" @click="deleteManualProperty(row)">删除属性</el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <el-card v-if="relationMappingTable.length > 0" class="property-mapping-card">
        <template #header>
          <div class="card-header">
            <div class="relation-card-heading">
              <span>当前实体关系与实现配置</span>
              <span class="relation-card-hint">边表始终连接本体节点表；仅在下方维护用于证明关系的源数据 Join</span>
            </div>
            <div class="header-actions">
              <el-checkbox v-model="showOnlyManualReview">仅显示待人工确认（{{ manualReviewRelationCount }}）</el-checkbox>
              <el-button size="small" @click="applyAllRelationDrafts">
                采用全部草案
              </el-button>
              <el-button type="primary" size="small" :loading="relationSaveLoading" @click="saveRelationMappings">
                保存关系实现
              </el-button>
            </div>
          </div>
        </template>

        <el-table :data="visibleRelationRows" :row-class-name="relationRowClassName" row-key="relation_id" border stripe size="small">
          <el-table-column prop="source_entity_name" label="本体源节点" min-width="170">
            <template #default="{ row }">
              <div class="ontology-node">
                <div class="ontology-node-role">
                  源节点
                  <el-tag v-if="row.source_entity_id === currentEntityId" size="small" type="primary">当前实体</el-tag>
                </div>
                <div class="ontology-node-name">{{ row.source_entity_name }}</div>
                <code class="ontology-node-table">{{ row.source_node_table }}</code>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="relation_name" label="本体关系" min-width="210">
            <template #default="{ row }">
              <div class="relation-config-title">{{ row.relation_name || '未命名关系' }}</div>
              <div class="relation-config-meta">
                <el-tag size="small" effect="plain">{{ row.relation_type || 'ASSOCIATION' }}</el-tag>
                <el-tag v-if="needsRelationReview(row)" size="small" type="warning">待人工确认</el-tag>
                <span v-if="row.blueprint_version">Blueprint v{{ row.blueprint_version }}</span>
              </div>
              <div v-if="needsRelationReview(row)" class="manual-review-hint">{{ relationReviewReason(row) }}</div>
            </template>
          </el-table-column>
          <el-table-column label="英文边表名" min-width="230">
            <template #default="{ row }">
              <el-input v-model="row.edge_table_name" size="small" placeholder="如 BELONGS_TO" />
              <div class="relation-config-meta">生成：ONTO_EDGE_{{ row.edge_table_name || '关系英文名' }}</div>
            </template>
          </el-table-column>
          <el-table-column prop="target_entity_name" label="本体目标节点" min-width="170">
            <template #default="{ row }">
              <div class="ontology-node">
                <div class="ontology-node-role">
                  目标节点
                  <el-tag v-if="row.target_entity_id === currentEntityId" size="small" type="primary">当前实体</el-tag>
                </div>
                <div class="ontology-node-name">{{ row.target_entity_name }}</div>
                <code class="ontology-node-table">{{ row.target_node_table }}</code>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="边关系实现" min-width="390">
            <template #default="{ row }">
              <el-radio-group v-model="row.mapping_mode" size="small" style="margin-bottom: 8px">
                <el-radio-button value="DIRECT">直接 Join</el-radio-button>
                <el-radio-button value="RELATION_TABLE">关系表模式</el-radio-button>
              </el-radio-group>
              <template v-if="row.mapping_mode === 'RELATION_TABLE'">
                <div class="relation-source-implementation">
                  <label class="relation-source-field"><span>关系证据表</span><el-select v-model="row.relation_table" size="small" filterable><el-option v-for="t in sourceTables" :key="`rel-${row.relation_id}-${t.table_name}`" :label="t.table_name" :value="t.table_name" /></el-select></label>
                  <label class="relation-source-field"><span>关系表 → 源节点字段</span><el-input v-model="row.relation_source_column" size="small" placeholder="如 BOTTLE_ID" /></label>
                  <label class="relation-source-field"><span>关系表 → 目标节点字段</span><el-input v-model="row.relation_target_column" size="small" placeholder="如 PACK_ID" /></label>
                </div>
              </template>
              <div v-if="row.mapping_mode !== 'RELATION_TABLE'" class="relation-source-implementation">
                <label class="relation-source-field">
                  <span>源节点表（src）</span>
                  <el-select v-model="row.source_table" size="small" placeholder="选择本体源节点表" filterable>
                    <el-option
                      v-for="node in nodeTableOptions"
                      :key="`src-${row.relation_id}-${node.table_name}`"
                      :label="node.label"
                      :value="node.table_name"
                    />
                  </el-select>
                </label>
                <label class="relation-source-field">
                  <span>目标节点表（dst）</span>
                  <el-select v-model="row.target_table" size="small" placeholder="选择本体目标节点表" filterable>
                    <el-option
                      v-for="node in nodeTableOptions"
                      :key="`dst-${row.relation_id}-${node.table_name}`"
                      :label="node.label"
                      :value="node.table_name"
                    />
                  </el-select>
                </label>
                <div class="relation-node-join-hint">
                  边表直接连接：{{ row.source_node_table }} → {{ row.target_node_table }}。Join 条件中的 src、dst 分别对应所选节点表。
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="join_condition" label="关系实现 Join 条件" min-width="240">
            <template #default="{ row }">
              <div v-if="row.mapping_mode === 'RELATION_TABLE'" class="join-candidate-meta">由关系表连接：src 节点主键 ← relation.{{ row.relation_source_column || '?' }}；relation.{{ row.relation_target_column || '?' }} → dst 节点主键</div>
              <div v-if="row.join_candidates?.length" class="join-candidate-list">
                <div v-for="candidate in row.join_candidates" :key="candidate.join_condition" class="join-candidate-item">
                  <div class="join-candidate-main">
                    <code>{{ candidate.join_condition }}</code>
                    <el-tag size="small" :type="joinCandidateTagType(candidate.status)">{{ joinCandidateStatusLabel(candidate.status) }}</el-tag>
                    <el-button
                      link
                      type="primary"
                      size="small"
                      :disabled="candidate.status !== 'VERIFIED'"
                      @click="applyJoinCandidate(row, candidate)"
                    >采用</el-button>
                  </div>
                  <div class="join-candidate-meta">
                    命中 {{ candidate.verification?.matched_count ?? 0 }} · 源覆盖 {{ formatCoverage(candidate.verification?.source_coverage) }} · {{ candidate.reason }}
                  </div>
                </div>
              </div>
              <el-input v-if="row.mapping_mode !== 'RELATION_TABLE'" v-model="row.join_condition" size="small" type="textarea" :rows="3" placeholder="如：src.vcm_id = dst.vcm_id" />
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120" fixed="right">
            <template #default="{ row }">
              <div class="row-actions">
                <el-button size="small" type="success" link :loading="row.join_analysis_loading" @click="analyzeJoin(row)">识别 Join</el-button>
                <el-button size="small" type="primary" link :disabled="!hasRelationDraft(row)" @click="applyRelationDraft(row)">
                  采用草案
                </el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </div>

    <el-empty v-if="!currentEntityId" description="请选择业务分析域、实体和数据源" />

  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute } from 'vue-router'
import { domainApi, entityApi, graphApi, mappingApi, propertyApi, sourceApi } from '../../api'
import { useAppStore } from '../../stores/app'

interface DataSourceOption {
  source_id: string
  source_name: string
  schema_name?: string | null
  is_default?: string
}

interface RemoteTableOption {
  owner: string
  table_name: string
  comments?: string | null
}

interface RemoteColumnOption {
  column_name: string
  data_type: string
  comments?: string | null
  mapping_supported?: boolean
  mapping_excluded_reason?: string | null
}

interface MappingRow {
  local_id: string
  property_id?: string
  mapping_id?: string
  property_name: string
  property_display_name: string
  property_desc: string
  data_type: string
  source_table: string
  source_column: string
  source_data_type: string
  mapping_type: string
  formula_expr: string
  formula_desc: string
  confidence: string
  mapping_status: string
  source_mark?: string
}

interface MappingGraphNode {
  id: string
  name: string
  displayName?: string
  buildType?: string
  status?: string
  propertiesCount: number
  mappedCount: number
}

interface MappingGraphEdge {
  id: string
  source: string
  target: string
  name?: string
  type?: string
}

interface RelationMappingRow {
  relation_id: string
  mapping_id?: string
  relation_name: string
  relation_type: string
  source_entity_id: string
  target_entity_id: string
  source_entity_name: string
  target_entity_name: string
  source_node_table: string
  target_node_table: string
  edge_table_name: string
  source_table: string
  target_table: string
  join_condition: string
  edge_sql: string
  mapping_status: string
  mapping_mode: string
  relation_table: string
  relation_source_column: string
  relation_target_column: string
  draft_source_table?: string
  draft_target_table?: string
  draft_join_condition?: string
  draft_edge_sql?: string
  blueprint_version?: number | string
  join_candidates?: any[]
  join_analysis_loading?: boolean
}

const appStore = useAppStore()
const route = useRoute()
const currentDomainId = ref(appStore.currentDomainId || '')
const currentEntityId = ref('')
const selectedSourceId = ref('')
const selectedSchema = ref('')

const domains = ref<any[]>([])
const entities = ref<any[]>([])
const dataSources = ref<DataSourceOption[]>([])
const schemaOptions = ref<string[]>([])
const sourceTables = ref<RemoteTableOption[]>([])
const tableColumnsMap = ref<Record<string, RemoteColumnOption[]>>({})

const entityMapping = ref<any>({ build_type: 'TABLE', mapping_status: 'PENDING' })
const mappingTable = ref<MappingRow[]>([])
const relationMappingTable = ref<RelationMappingRow[]>([])
const mappingNodes = ref<MappingGraphNode[]>([])
const mappingEdges = ref<MappingGraphEdge[]>([])

const schemaLoading = ref(false)
const sourceTableLoading = ref(false)
const saveLoading = ref(false)
const relationSaveLoading = ref(false)
const showOnlyManualReview = ref(false)
const lastSavedSnapshot = ref('')

const mappedCount = computed(() => mappingTable.value.filter(row => row.source_table && row.source_column).length)
const totalPropertyCount = computed(() => mappingTable.value.length)
const mappingProgress = computed(() => totalPropertyCount.value ? Math.round(mappedCount.value / totalPropertyCount.value * 100) : 0)
const mappedEntityCount = computed(() => mappingNodes.value.filter(node => node.mappedCount > 0).length)
const hasUnsavedChanges = computed(() => buildSnapshot() !== lastSavedSnapshot.value)
const currentEntityLabel = computed(() => {
  const currentEntity = entities.value.find(item => item.entity_id === currentEntityId.value)
  return currentEntity?.entity_display_name || currentEntity?.entity_name || '当前实体'
})
const currentEntityRows = computed(() => mappingTable.value)
const currentEntityMappedRows = computed(() => mappingTable.value.filter(row => row.source_table && row.source_column))
const currentEntityPendingCount = computed(() => Math.max(mappingTable.value.length - currentEntityMappedRows.value.length, 0))
const relatedEdges = computed(() => mappingEdges.value.filter(edge => edge.source === currentEntityId.value || edge.target === currentEntityId.value))
const needsPropertyReview = (row: MappingRow) => {
  const confidence = (row.confidence || '').toUpperCase()
  // “待确认”只反映无法落地或低可信的实际问题；全域映射已确认的
  // SUGGESTED/STALE 状态不应在管理页被重复要求人工确认。
  return !row.source_table || !row.source_column || confidence === 'LOW'
}
const propertyReviewReason = (row: MappingRow) => {
  if (!row.source_table || !row.source_column) return '缺少源表或源字段'
  if ((row.confidence || '').toUpperCase() === 'LOW') return '低置信度建议，需核对字段语义'
  return ''
}
const needsRelationReview = (row: RelationMappingRow) => {
  const mode = (row.mapping_mode || 'DIRECT').toUpperCase()
  if (mode === 'RELATION_TABLE') {
    return !row.relation_table || !row.relation_source_column || !row.relation_target_column
  }
  return !row.source_table || !row.target_table || !row.join_condition
}
const relationReviewReason = (row: RelationMappingRow) => {
  if ((row.mapping_mode || 'DIRECT').toUpperCase() === 'RELATION_TABLE') return '请补齐关系证据表及其两端关联字段'
  if (!row.source_table || !row.target_table) return '缺少两端节点来源表'
  if (!row.join_condition) return '缺少已验证的业务 Join 条件'
  return ''
}
const manualReviewPropertyCount = computed(() => mappingTable.value.filter(needsPropertyReview).length)
const manualReviewRelationCount = computed(() => relationMappingTable.value.filter(needsRelationReview).length)
const visibleMappingRows = computed(() => showOnlyManualReview.value ? mappingTable.value.filter(needsPropertyReview) : mappingTable.value)
const visibleRelationRows = computed(() => showOnlyManualReview.value ? relationMappingTable.value.filter(needsRelationReview) : relationMappingTable.value)
const mappingRowClassName = ({ row }: { row: MappingRow }) => needsPropertyReview(row) ? 'manual-review-row' : ''
const relationRowClassName = ({ row }: { row: RelationMappingRow }) => needsRelationReview(row) ? 'manual-review-row' : ''
const currentEntitySourceTables = computed(() => Array.from(new Set(
  mappingTable.value
    .filter(row => row.source_table && row.source_column && ['DIRECT', 'COMPUTED'].includes((row.mapping_type || '').toUpperCase()))
    .map(row => row.source_table.trim().toUpperCase())
)))
const entityDDLReady = computed(() => {
  const hasViewSql = Boolean((entityMapping.value?.view_sql || '').trim())
  if (hasViewSql) return true
  if (!mappingTable.value.length) return false
  const allPropertiesSourceReady = mappingTable.value.every(row => {
    const mappingType = (row.mapping_type || '').toUpperCase()
    if (!row.source_table.trim() || !row.source_column.trim()) return false
    if (mappingType === 'DIRECT') return true
    if (mappingType === 'COMPUTED') return Boolean(row.formula_expr.trim())
    return false
  })
  return allPropertiesSourceReady && currentEntitySourceTables.value.length <= 1
})
const entityDDLReason = computed(() => {
  const hasViewSql = Boolean((entityMapping.value?.view_sql || '').trim())
  if (hasViewSql) return '已维护 view_sql'
  if (!mappingTable.value.length) return '当前实体暂无属性'
  const allPropertiesSourceReady = mappingTable.value.every(row => {
    const mappingType = (row.mapping_type || '').toUpperCase()
    if (!row.source_table.trim() || !row.source_column.trim()) return false
    if (mappingType === 'DIRECT') return true
    if (mappingType === 'COMPUTED') return Boolean(row.formula_expr.trim())
    return false
  })
  if (!allPropertiesSourceReady) return '存在属性未确认来源'
  if (currentEntitySourceTables.value.length > 1) return '跨多源表，需维护 view_sql'
  if (currentEntitySourceTables.value.length === 1) return `单源表 ${currentEntitySourceTables.value[0]}`
  return '映射草案'
})

const mappingStatusTagType = (status: string) => {
  if (status === 'CONFIRMED') return 'success'
  if (status === 'REJECTED') return 'danger'
  if (status === 'SUGGESTED') return 'info'
  return 'warning'
}

const mappingStatusLabel = (status: string) => ({
  CONFIRMED: '已确认',
  PENDING: '待补齐',
  SUGGESTED: '待确认',
  STALE: '需更新DDL',
  REJECTED: '已拒绝'
}[status] || status || '待补齐')

const confidenceTagType = (confidence: string) => {
  if (confidence === 'HIGH') return 'success'
  if (confidence === 'MEDIUM') return 'warning'
  return 'danger'
}

const formatEntityStatus = (status?: string) => {
  if (status === 'MAPPED') return '已映射'
  if (status === 'DDL_GENERATED') return '已生成DDL'
  if (status === 'DEPLOYED') return '已部署'
  return '系统建议'
}

const getNodeProgress = (node: MappingGraphNode) => {
  if (!node.propertiesCount) return 0
  return Math.round((node.mappedCount / node.propertiesCount) * 100)
}

const getEntityName = (entityId: string) => {
  const node = mappingNodes.value.find(item => item.id === entityId)
  return node?.displayName || node?.name || entityId
}

const getEntityNodeTable = (entityId: string) => {
  const entity = entities.value.find(item => item.entity_id === entityId)
  const configuredTable = (entity?.table_name || '').trim().toUpperCase()
  if (configuredTable) return configuredTable
  const entityName = (entity?.entity_name || getEntityName(entityId) || entityId)
    .replace(/[^A-Za-z0-9_]/g, '_')
    .toUpperCase()
  return `ONTO_NODE_${entityName}`
}

const nodeTableOptions = computed(() => entities.value.map(entity => ({
  table_name: getEntityNodeTable(entity.entity_id),
  label: `${getEntityNodeTable(entity.entity_id)}（${entity.entity_display_name || entity.entity_name}）`,
})))

const createLocalRowId = () => `row_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`

const buildSnapshot = () => JSON.stringify(
  {
    entity_mapping: {
      build_type: entityMapping.value?.build_type || '',
      view_sql: entityMapping.value?.view_sql || ''
    },
    properties: mappingTable.value.map((row, index) => ({
      order_num: index + 1,
      property_id: row.property_id || '',
      property_name: row.property_name || '',
      property_display_name: row.property_display_name || '',
      property_desc: row.property_desc || '',
      data_type: row.data_type || '',
      source_table: row.source_table || '',
      source_column: row.source_column || '',
      source_data_type: row.source_data_type || '',
      mapping_type: row.mapping_type || '',
      formula_expr: row.formula_expr || '',
      formula_desc: row.formula_desc || '',
      confidence: row.confidence || '',
      mapping_status: row.mapping_status || ''
    }))
  }
)

const syncSnapshot = () => {
  lastSavedSnapshot.value = buildSnapshot()
}

const getSourceColumns = (tableName: string) => {
  return (tableColumnsMap.value[tableName] || []).filter(column => column.mapping_supported !== false)
}

const findRemoteColumn = (tableName: string, columnName: string) => {
  return getSourceColumns(tableName).find(column => column.column_name === columnName)
}

const preloadSourceColumns = async (tableName: string) => {
  if (!tableName || !selectedSourceId.value || !selectedSchema.value) return
  if (tableColumnsMap.value[tableName]) return
  const res = await sourceApi.getRemoteTableDetail(selectedSourceId.value, tableName, {
    schema: selectedSchema.value,
    sample_limit: 1
  })
  tableColumnsMap.value = {
    ...tableColumnsMap.value,
    [tableName]: res.data?.columns || []
  }
}

const loadDomains = async () => {
  try {
    const res = await domainApi.list('ACTIVE')
    domains.value = res.data || []
  } catch (e) {}
}

const loadEntities = async () => {
  if (!currentDomainId.value) return
  try {
    const res = await entityApi.list(currentDomainId.value)
    entities.value = res.data || []
    const routeEntityId = typeof route.query.entity_id === 'string' ? route.query.entity_id : ''
    if (routeEntityId && entities.value.some(item => item.entity_id === routeEntityId)) {
      currentEntityId.value = routeEntityId
    }
  } catch (e) {}
}

const loadDataSources = async () => {
  try {
    const res = await sourceApi.listDataSources(currentDomainId.value || undefined)
    dataSources.value = res.data || []
    if (!selectedSourceId.value && dataSources.value.length) {
      const defaultSource = dataSources.value.find(item => item.is_default === 'Y') || dataSources.value[0]
      selectedSourceId.value = defaultSource.source_id
      await handleSourceChange()
    }
  } catch (e) {}
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

const loadSourceTables = async () => {
  if (!selectedSourceId.value || !selectedSchema.value) return
  sourceTableLoading.value = true
  try {
    const res = await sourceApi.getRemoteTables(selectedSourceId.value, { schema: selectedSchema.value })
    sourceTables.value = res.data?.tables || []
    tableColumnsMap.value = {}
  } finally {
    sourceTableLoading.value = false
  }
}

const loadEntityMapping = async () => {
  if (!currentEntityId.value) return
  try {
    const res = await mappingApi.getEntityMapping(currentEntityId.value)
    entityMapping.value = res.data || {}
  } catch (e) {}
}

const loadPropertyMappings = async () => {
  if (!currentEntityId.value) return
  try {
    const [propertyRes, mappingRes] = await Promise.all([
      propertyApi.list(currentEntityId.value),
      mappingApi.getPropertyMappings(currentEntityId.value)
    ])
    const properties = propertyRes.data || []
    const mappings = mappingRes.data || []
    mappingTable.value = properties.map((prop: Record<string, any>) => {
      const mapping = mappings.find((item: Record<string, any>) => item.property_id === prop.property_id)
      return {
        local_id: createLocalRowId(),
        property_id: prop.property_id,
        mapping_id: mapping?.mapping_id,
        property_name: prop.property_name || '',
        property_display_name: prop.property_display_name || '',
        property_desc: prop.property_desc || '',
        data_type: prop.data_type || '',
        source_table: mapping?.source_table || '',
        source_column: mapping?.source_column || '',
        source_data_type: '',
        mapping_type: mapping?.mapping_type || 'DIRECT',
        formula_expr: mapping?.formula_expr || '',
        formula_desc: mapping?.formula_desc || '',
        confidence: mapping?.confidence || '',
        mapping_status: mapping?.mapping_status || 'PENDING',
        source_mark: prop.source_mark || 'PENDING'
      }
    })

    const preloadTasks = mappingTable.value
      .filter(row => row.source_table)
      .map(async row => {
        await preloadSourceColumns(row.source_table)
        const column = findRemoteColumn(row.source_table, row.source_column)
        row.source_data_type = column?.data_type || row.data_type
      })
    await Promise.all(preloadTasks)
    syncSnapshot()
  } catch (e) {}
}

const loadRelationMappings = async () => {
  if (!currentEntityId.value) {
    relationMappingTable.value = []
    return
  }
  const edges = relatedEdges.value
  if (!edges.length) {
    relationMappingTable.value = []
    return
  }
  try {
    const responses = await Promise.all(edges.map(edge => mappingApi.getRelationMapping(edge.id)))
    relationMappingTable.value = edges.map((edge, index) => {
      const mapping = responses[index]?.data || null
      return {
        relation_id: edge.id,
        mapping_id: mapping?.mapping_id || '',
        relation_name: edge.name || '',
        relation_type: edge.type || '',
        source_entity_id: edge.source,
        target_entity_id: edge.target,
        source_entity_name: getEntityName(edge.source),
        target_entity_name: getEntityName(edge.target),
        source_node_table: getEntityNodeTable(edge.source),
        target_node_table: getEntityNodeTable(edge.target),
        edge_table_name: mapping?.edge_table_name || '',
        source_table: mapping?.source_table?.toUpperCase().startsWith('ONTO_NODE_') ? mapping.source_table : getEntityNodeTable(edge.source),
        target_table: mapping?.target_table?.toUpperCase().startsWith('ONTO_NODE_') ? mapping.target_table : getEntityNodeTable(edge.target),
        join_condition: mapping?.join_condition || '',
        edge_sql: mapping?.edge_sql || '',
        mapping_status: mapping?.mapping_status || 'PENDING',
        mapping_mode: mapping?.mapping_mode || 'DIRECT',
        relation_table: mapping?.relation_table || '',
        relation_source_column: mapping?.relation_source_column || '',
        relation_target_column: mapping?.relation_target_column || '',
        draft_source_table: mapping?.draft?.source_table || '',
        draft_target_table: mapping?.draft?.target_table || '',
        draft_join_condition: mapping?.draft?.join_condition || '',
        draft_edge_sql: mapping?.draft?.edge_sql || '',
        blueprint_version: mapping?.blueprint_version || ''
      }
    })
  } catch (e) {
    relationMappingTable.value = edges.map(edge => ({
      relation_id: edge.id,
      mapping_id: '',
      relation_name: edge.name || '',
      relation_type: edge.type || '',
      source_entity_id: edge.source,
      target_entity_id: edge.target,
      source_entity_name: getEntityName(edge.source),
      target_entity_name: getEntityName(edge.target),
      source_node_table: getEntityNodeTable(edge.source),
      target_node_table: getEntityNodeTable(edge.target),
      edge_table_name: '',
      source_table: '',
      target_table: '',
      join_condition: '',
      edge_sql: '',
      mapping_status: 'PENDING',
      mapping_mode: 'DIRECT',
      relation_table: '',
      relation_source_column: '',
      relation_target_column: '',
      draft_source_table: '',
      draft_target_table: '',
      draft_join_condition: '',
      draft_edge_sql: '',
      blueprint_version: ''
    }))
  }
}

const hasRelationDraft = (row: RelationMappingRow) => {
  return Boolean(row.draft_source_table || row.draft_target_table || row.draft_join_condition)
}

const applyRelationDraft = (row: RelationMappingRow) => {
  if (!hasRelationDraft(row)) return
  row.source_table = row.draft_source_table || row.source_table
  row.target_table = row.draft_target_table || row.target_table
  row.join_condition = row.draft_join_condition || row.join_condition
  if (row.mapping_status !== 'CONFIRMED') {
    row.mapping_status = 'SUGGESTED'
  }
  ElMessage.success(`已采用关系「${row.relation_name || row.relation_id}」的草案`)
}

const applyAllRelationDrafts = () => {
  let applied = 0
  relationMappingTable.value.forEach(row => {
    if (hasRelationDraft(row)) {
      row.source_table = row.draft_source_table || row.source_table
      row.target_table = row.draft_target_table || row.target_table
      row.join_condition = row.draft_join_condition || row.join_condition
      if (row.mapping_status !== 'CONFIRMED') {
        row.mapping_status = 'SUGGESTED'
      }
      applied += 1
    }
  })
  if (applied) ElMessage.success(`已采用 ${applied} 条关系草案`)
}

const joinCandidateStatusLabel = (status: string) => ({ VERIFIED: '已验证', NO_MATCH: '零命中', UNVERIFIED: '未验证' }[status] || status)
const joinCandidateTagType = (status: string) => ({ VERIFIED: 'success', NO_MATCH: 'danger', UNVERIFIED: 'warning' }[status] || 'info')
const formatCoverage = (value: unknown) => `${Math.round(Number(value || 0) * 100)}%`

const applyJoinCandidate = (row: RelationMappingRow, candidate: any) => {
  if (candidate?.status !== 'VERIFIED') return
  row.join_condition = candidate.join_condition
  row.mapping_status = 'SUGGESTED'
  ElMessage.success(`已采用已验证的 Join：${candidate.join_condition}`)
}

const analyzeJoin = async (row: RelationMappingRow) => {
  if (!selectedSourceId.value) {
    ElMessage.warning('请先选择数据库连接')
    return
  }
  if (!row.source_table || !row.target_table) {
    ElMessage.warning('请先选择源节点和目标节点的来源表')
    return
  }
  row.join_analysis_loading = true
  try {
    const res = await mappingApi.analyzeRelationJoin(row.relation_id, {
      source_id: selectedSourceId.value,
      schema: selectedSchema.value || undefined,
      source_table: row.source_table,
      target_table: row.target_table,
      join_condition: row.join_condition || undefined
    })
    const candidates = res.data?.candidates || []
    row.join_candidates = candidates
    if (!candidates.length) ElMessage.warning('未找到可验证的 Join 候选，请检查属性映射或来源表')
  } catch (e: any) {
    ElMessage.error(e?.message || 'Join 识别失败')
  } finally {
    row.join_analysis_loading = false
  }
}

const loadMappingGraph = async () => {
  if (!currentDomainId.value) return
  try {
    const res = await graphApi.getOntologyGraph(currentDomainId.value)
    mappingNodes.value = res.data?.nodes || []
    mappingEdges.value = res.data?.edges || []
  } catch (e) {}
}

const handleDomainChange = async () => {
  const domain = domains.value.find(item => item.domain_id === currentDomainId.value)
  appStore.setCurrentDomain(currentDomainId.value, domain?.domain_name || '')
  currentEntityId.value = ''
  mappingTable.value = []
  syncSnapshot()
  await loadEntities()
  await loadMappingGraph()
  if (entities.value.length > 0) {
    currentEntityId.value = entities.value[0].entity_id
    await handleEntityChange()
  }
}

const switchCurrentEntity = async (entityId: string) => {
  if (!entityId || entityId === currentEntityId.value) return

  if (hasUnsavedChanges.value) {
    try {
      await ElMessageBox.confirm('当前实体有未保存修改，切换对象后这些修改不会自动保存。是否继续切换？', '切换本体对象', {
        type: 'warning',
        confirmButtonText: '继续切换',
        cancelButtonText: '取消'
      })
    } catch (e) {
      return
    }
  }

  currentEntityId.value = entityId
  await handleEntityChange()
}

const handleEntityChange = async () => {
  await loadEntityMapping()
  await loadPropertyMappings()
  await loadRelationMappings()
}

const handleSourceChange = async () => {
  selectedSchema.value = ''
  sourceTables.value = []
  tableColumnsMap.value = {}
  await loadSchemas()
  await loadSourceTables()
}

const handleSchemaChange = async () => {
  sourceTables.value = []
  tableColumnsMap.value = {}
  await loadSourceTables()
  await loadPropertyMappings()
}

const handleSourceTableChange = async (row: MappingRow) => {
  row.source_column = ''
  row.source_data_type = ''
  row.mapping_status = 'PENDING'
  if (!row.source_table) return
  await preloadSourceColumns(row.source_table)
}

const handleSourceColumnChange = (row: MappingRow) => {
  const column = findRemoteColumn(row.source_table, row.source_column)
  row.source_data_type = column?.data_type || ''
  if (!row.data_type && row.source_data_type) {
    row.data_type = row.source_data_type
  }
  const mappingType = (row.mapping_type || '').toUpperCase()
  row.mapping_status = row.source_table && row.source_column && (mappingType !== 'COMPUTED' || row.formula_expr.trim()) ? 'CONFIRMED' : 'PENDING'
}

const addManualProperty = () => {
  mappingTable.value.push({
    local_id: createLocalRowId(),
    property_name: '',
    property_display_name: '',
    property_desc: '',
    data_type: 'VARCHAR2',
    source_table: '',
    source_column: '',
    source_data_type: '',
    mapping_type: 'DIRECT',
    formula_expr: '',
    formula_desc: '',
    confidence: '',
    mapping_status: 'PENDING',
    source_mark: 'PENDING'
  })
}

const clearRowMapping = (row: MappingRow) => {
  row.source_table = ''
  row.source_column = ''
  row.source_data_type = ''
  row.formula_expr = ''
  row.confidence = ''
  row.mapping_status = 'PENDING'
  row.source_mark = 'PENDING'
}

const validateMappingRows = () => {
  const seenNames = new Set<string>()
  for (const row of mappingTable.value) {
    const propertyName = row.property_name.trim()
    if (!propertyName) {
      ElMessage.warning('本体属性名称不能为空')
      return false
    }
    const normalizedName = propertyName.toLowerCase()
    if (seenNames.has(normalizedName)) {
      ElMessage.warning(`属性名称重复：${propertyName}`)
      return false
    }
    seenNames.add(normalizedName)

    if (row.source_column && !row.source_table) {
      ElMessage.warning(`属性 ${propertyName} 选择源字段前请先选择源表`)
      return false
    }
    if (row.source_table && !row.source_column) {
      ElMessage.warning(`属性 ${propertyName} 选择源表后必须继续选择源字段`)
      return false
    }
    if (row.mapping_type === 'COMPUTED' && !row.formula_expr.trim()) {
      ElMessage.warning(`属性 ${propertyName} 选择计算映射时必须填写计算公式`)
      return false
    }
  }
  return true
}

const saveManualMappings = async () => {
  if (!currentEntityId.value) return
  if (!validateMappingRows()) return

  saveLoading.value = true
  try {
    for (let index = 0; index < mappingTable.value.length; index += 1) {
      const row = mappingTable.value[index]
      const propertyPayload = {
        property_name: row.property_name.trim(),
        property_display_name: row.property_display_name.trim() || null,
        data_type: row.data_type.trim() || 'VARCHAR2',
        is_primary_key: 'N',
        is_nullable: 'Y',
        property_desc: row.property_desc.trim() || null,
        order_num: index + 1
      }

      if (!row.property_id) {
        const createRes = await propertyApi.create(currentEntityId.value, propertyPayload)
        row.property_id = createRes.data?.property_id
      } else {
        await propertyApi.update(row.property_id, propertyPayload)
      }

      if (!row.property_id) continue

      const mappingType = (row.mapping_type || 'DIRECT').toUpperCase()
      const hasSourceBinding = Boolean(row.source_table && row.source_column)
      const propertyDDLReady = hasSourceBinding && (
        mappingType === 'DIRECT' ||
        (mappingType === 'COMPUTED' && Boolean(row.formula_expr.trim()))
      )
      const shouldPersistMapping = hasSourceBinding || Boolean(row.mapping_id)
      if (shouldPersistMapping) {
        const mappingPayload = {
          source_table: hasSourceBinding ? row.source_table : null,
          source_column: hasSourceBinding ? row.source_column : null,
          mapping_type: mappingType,
          formula_expr: row.mapping_type === 'COMPUTED' ? (row.formula_expr.trim() || null) : null,
          formula_desc: row.formula_desc.trim() || null,
          confidence: propertyDDLReady ? (row.confidence || 'MEDIUM') : null,
          mapping_status: propertyDDLReady ? 'CONFIRMED' : 'PENDING'
        }
        await mappingApi.updatePropertyMapping(row.property_id, mappingPayload)
        row.mapping_status = mappingPayload.mapping_status
      } else {
        row.mapping_status = 'PENDING'
      }

      row.source_mark = propertyDDLReady ? 'MAPPED' : 'PENDING'
      if (propertyDDLReady && !row.confidence) {
        row.confidence = 'MEDIUM'
      }
    }

    entityMapping.value.mapping_status = entityDDLReady.value ? 'CONFIRMED' : 'PENDING'
    await mappingApi.updateEntityMapping(currentEntityId.value, {
      build_type: entityMapping.value.build_type || 'TABLE',
      view_sql: entityMapping.value.view_sql?.trim() || null,
      mapping_status: entityMapping.value.mapping_status
    })

    await loadEntities()
    await loadPropertyMappings()
    await loadMappingGraph()
    ElMessage.success('属性映射已保存')
  } catch (e) {
  } finally {
    saveLoading.value = false
  }
}

const deleteManualProperty = async (row: MappingRow) => {
  const propertyLabel = row.property_display_name || row.property_name || '该属性'
  try {
    await ElMessageBox.confirm(`确认删除 ${propertyLabel} 吗？`, '删除属性', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消'
    })
  } catch (e) {
    return
  }

  if (!row.property_id) {
    mappingTable.value = mappingTable.value.filter(item => item.local_id !== row.local_id)
    syncSnapshot()
    return
  }

  try {
    await propertyApi.delete(row.property_id)
    mappingTable.value = mappingTable.value.filter(item => item.local_id !== row.local_id)
    syncSnapshot()
    await loadEntities()
    await loadPropertyMappings()
    await loadMappingGraph()
    ElMessage.success('属性已删除')
  } catch (e) {}
}

const saveRelationMappings = async () => {
  if (!relationMappingTable.value.length) return
  relationSaveLoading.value = true
  try {
    for (const row of relationMappingTable.value) {
      const hasMappingContent = Boolean(
        row.edge_table_name.trim() ||
        row.source_table.trim() ||
        row.target_table.trim() ||
        row.join_condition.trim() ||
        row.relation_table.trim()
      )
      const relationTableReady = row.mapping_mode === 'RELATION_TABLE' && row.relation_table.trim() && row.relation_source_column.trim() && row.relation_target_column.trim()
      if (row.mapping_mode === 'RELATION_TABLE' && !relationTableReady) {
        ElMessage.warning(`请完整配置关系「${row.relation_name || row.relation_id}」的关系表及两端字段`)
        return
      }
      if (!hasMappingContent) {
        row.mapping_status = 'PENDING'
        continue
      }
      const payload = {
        relation_id: row.relation_id,
        edge_table_name: row.edge_table_name.trim() || null,
        source_table: row.source_table.trim() || null,
        target_table: row.target_table.trim() || null,
        join_condition: row.join_condition.trim() || null,
        mapping_status: 'SUGGESTED',
        mapping_mode: row.mapping_mode,
        relation_table: row.relation_table.trim() || null,
        relation_source_column: row.relation_source_column.trim() || null,
        relation_target_column: row.relation_target_column.trim() || null,
      }
      if (row.mapping_id) {
        await mappingApi.updateRelationMapping(row.relation_id, payload)
      } else {
        const res = await mappingApi.createRelationMapping(row.relation_id, payload)
        row.mapping_id = res.data?.mapping_id || row.mapping_id
      }
      row.mapping_status = payload.mapping_status
    }
    await loadRelationMappings()
    ElMessage.success('关系映射已保存')
  } catch (e) {
  } finally {
    relationSaveLoading.value = false
  }
}

watch(() => appStore.currentDomainId, async (domainId) => {
  if (domainId === currentDomainId.value) return
  currentDomainId.value = domainId || ''
  currentEntityId.value = ''
  selectedSourceId.value = ''
  selectedSchema.value = ''
  mappingTable.value = []
  mappingNodes.value = []
  mappingEdges.value = []
  if (!currentDomainId.value) {
    entities.value = []
    dataSources.value = []
    return
  }
  await Promise.all([loadEntities(), loadDataSources(), loadMappingGraph()])
  if (entities.value.length) {
    currentEntityId.value = entities.value[0].entity_id
    await handleEntityChange()
  }
})

onMounted(async () => {
  await loadDomains()
  await loadDataSources()
  if (currentDomainId.value) {
    await loadEntities()
    await loadMappingGraph()
    if (!currentEntityId.value && entities.value.length > 0) {
      currentEntityId.value = entities.value[0].entity_id
    }
    if (currentEntityId.value) await handleEntityChange()
  }
})
</script>

<style scoped>
.mapping-page {
  height: calc(100vh - 90px);
  overflow-y: auto;
}

.top-bar {
  display: flex;
  gap: 12px;
  padding: 10px 0;
  flex-wrap: wrap;
}

.toolbar-select {
  width: 220px;
}

.mapping-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-actions {
  display: flex;
  gap: 8px;
}

.mapping-progress {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
  font-size: 13px;
  color: #666;
}

.source-context {
  color: #31557d;
  font-size: 13px;
}

.row-actions {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  line-height: 1.4;
}

.mapping-preview {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.mapping-preview-summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
}

.summary-item {
  padding: 14px 16px;
  border: 1px solid #e6edf5;
  border-radius: 10px;
  background: linear-gradient(180deg, #f9fbff 0%, #f3f7fc 100%);
}

.summary-label {
  font-size: 12px;
  color: #60758a;
}

.summary-value {
  margin-top: 6px;
  font-size: 24px;
  font-weight: 700;
  color: #1f3b57;
}

.mapping-preview-layout {
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: 16px;
}

.preview-panel {
  padding: 16px;
  border: 1px solid #e6edf5;
  border-radius: 10px;
  background: #fff;
}

.preview-title {
  font-size: 15px;
  font-weight: 600;
  color: #1f3b57;
}

.preview-subtitle {
  margin-top: 6px;
  margin-bottom: 14px;
  font-size: 12px;
  line-height: 1.6;
  color: #6a7d91;
}

.entity-overview-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.entity-overview-card {
  padding: 14px;
  border-radius: 10px;
  border: 1px solid #e8eef5;
  background: #fbfdff;
  cursor: pointer;
  transition: border-color 0.2s ease, box-shadow 0.2s ease, transform 0.2s ease;
}

.entity-overview-card:hover {
  border-color: #9bb8d8;
  box-shadow: 0 6px 16px rgba(70, 98, 130, 0.08);
  transform: translateY(-1px);
}

.entity-overview-card.active {
  border-color: #7aa8d8;
  box-shadow: inset 0 0 0 1px #cfe0f3;
  background: #f6faff;
}

.entity-overview-header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
}

.entity-overview-name {
  font-size: 14px;
  font-weight: 600;
  color: #1f3b57;
}

.entity-overview-meta {
  display: flex;
  justify-content: space-between;
  margin: 10px 0 12px;
  font-size: 12px;
  color: #6a7d91;
}

.mapped-property-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.mapped-property-item {
  padding: 12px 14px;
  border-radius: 10px;
  border: 1px solid #e8eef5;
  background: #fbfdff;
}

.mapped-property-main {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.mapped-property-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.mapped-property-name {
  font-size: 14px;
  font-weight: 600;
  color: #1f3b57;
}

.mapped-property-source {
  margin-top: 6px;
  font-size: 12px;
  color: #58708a;
  word-break: break-all;
}

.mapped-property-reason {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.5;
  color: #7b8da1;
}

.relation-panel {
  padding-top: 14px;
}

.relation-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.relation-item {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  border-radius: 10px;
  border: 1px solid #e8eef5;
  background: #fbfdff;
}

.relation-entity {
  color: #1f3b57;
  font-weight: 600;
}

.relation-arrow {
  color: #7b8da1;
}

.relation-name {
  color: #58708a;
  font-size: 12px;
}

.relation-card-heading {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.relation-card-hint {
  color: #6a7d91;
  font-size: 12px;
  font-weight: 400;
}

.manual-review-alert {
  margin-bottom: 14px;
}

.manual-review-hint {
  margin-top: 6px;
  color: #a36a00;
  font-size: 11px;
  line-height: 1.35;
}

:deep(.el-table .manual-review-row > td.el-table__cell) {
  background: #fff8e6 !important;
}

.ontology-node {
  padding: 10px 12px;
  border: 1px solid #dfe9f3;
  border-radius: 8px;
  background: #f8fbff;
}

.ontology-node-role {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: #6a7d91;
  font-size: 11px;
}

.ontology-node-name {
  margin-top: 6px;
  color: #1f3b57;
  font-size: 13px;
  font-weight: 600;
  word-break: break-word;
}

.ontology-node-table {
  display: block;
  margin-top: 6px;
  color: #3d6f9e;
  font-size: 11px;
  word-break: break-all;
}

.relation-config-title {
  font-size: 13px;
  font-weight: 600;
  color: #1f3b57;
}

.relation-config-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  font-size: 12px;
  color: #6a7d91;
}

.relation-source-implementation {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 10px;
}

.relation-source-field {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 6px;
  color: #58708a;
  font-size: 11px;
}

.relation-source-field :deep(.el-select) {
  width: 100%;
}

.join-candidate-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 8px;
}

.join-candidate-item {
  padding: 7px 8px;
  border: 1px solid #dfe9f3;
  border-radius: 6px;
  background: #f8fbff;
}

.join-candidate-main {
  display: flex;
  align-items: center;
  gap: 6px;
}

.relation-node-join-hint {
  grid-column: 1 / -1;
  color: #62788f;
  font-size: 11px;
  line-height: 1.5;
}

.join-candidate-main code {
  flex: 1;
  min-width: 0;
  color: #245b89;
  font-size: 12px;
  word-break: break-all;
}

.join-candidate-meta {
  margin-top: 5px;
  color: #6a7d91;
  font-size: 11px;
  line-height: 1.4;
}

@media (max-width: 960px) {
  .mapping-preview-layout {
    grid-template-columns: 1fr;
  }

  .relation-source-implementation {
    grid-template-columns: 1fr;
  }
}
</style>
