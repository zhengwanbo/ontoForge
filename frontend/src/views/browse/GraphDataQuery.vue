<template>
  <div class="graph-query-page">
    <section class="page-header">
      <div><div class="eyebrow">ORACLE PROPERTY GRAPH</div><h2>图数据查询</h2><p>在选定的目标 Oracle 数据库中执行只读 Graph SQL，并将包含 SOURCE_ID、TARGET_ID 的结果渲染为关系图。</p></div>
    </section>

    <el-card shadow="never" class="query-card">
      <div class="query-form">
        <el-select v-model="domainId" placeholder="选择业务分析域" @change="handleDomainChange"><el-option v-for="domain in domains" :key="domain.domain_id" :label="domain.domain_name" :value="domain.domain_id" /></el-select>
        <el-select v-model="sourceId" placeholder="选择目标 Oracle 数据库" filterable :disabled="!domainId" @change="handleSourceChange"><el-option v-for="source in sources" :key="source.source_id" :label="`${source.source_name} / ${source.schema_name || source.username}`" :value="source.source_id" /></el-select>
        <el-select v-model="schema" placeholder="Schema（可选）" filterable :disabled="!sourceId"><el-option v-for="item in schemas" :key="item" :label="item" :value="item" /></el-select>
        <el-input-number v-model="rowLimit" :min="1" :max="1000" controls-position="right" />
        <el-button type="primary" :loading="executing" :disabled="!canExecute" @click="executeQuery">执行 Graph SQL</el-button>
      </div>
      <div class="form-hint">只允许包含 <code>GRAPH_TABLE</code> 的只读 <code>SELECT / WITH</code> 查询；结果列建议命名为 <code>SOURCE_ID</code>、<code>TARGET_ID</code>、<code>RELATION_NAME</code>。</div>
      <el-input v-model="graphSql" type="textarea" :rows="10" resize="vertical" class="sql-editor" />
    </el-card>

    <el-alert v-if="result && !graphData.edges.length" type="info" :closable="false" show-icon class="result-alert"><template #title>查询返回 {{ result.rows?.length || 0 }} 行，但未找到 SOURCE_ID / TARGET_ID；下方保留表格结果。为渲染关系图，请在 SQL 中为两端顶点 ID 使用这两个别名。</template></el-alert>

    <div v-if="result" class="result-layout">
      <el-card shadow="never" class="graph-result-card">
        <template #header><div class="card-header"><span>查询图形结果</span><el-tag>{{ graphData.nodes.length }} 个节点 / {{ graphData.edges.length }} 条边</el-tag></div></template>
        <div class="graph-canvas">
          <svg v-if="graphData.nodes.length" viewBox="0 0 1100 620" class="graph-svg">
            <defs><marker id="query-arrow" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto"><path d="M0,0 L9,4.5 L0,9 z" fill="#64748b" /></marker></defs>
            <g v-for="edge in graphData.edges" :key="edge.id"><line :x1="nodePosition(edge.source).x" :y1="nodePosition(edge.source).y" :x2="nodePosition(edge.target).x" :y2="nodePosition(edge.target).y" class="edge-line" marker-end="url(#query-arrow)" /><text :x="(nodePosition(edge.source).x + nodePosition(edge.target).x) / 2" :y="(nodePosition(edge.source).y + nodePosition(edge.target).y) / 2 - 8" class="edge-text">{{ edge.label }}</text></g>
            <g v-for="node in graphData.nodes" :key="node.id" :transform="`translate(${nodePosition(node.id).x}, ${nodePosition(node.id).y})`" class="query-node"><circle r="35" /><text y="4">{{ node.label }}</text></g>
          </svg>
          <el-empty v-else description="查询结果中没有可绘制的关系数据" />
        </div>
      </el-card>
      <el-card shadow="never" class="table-result-card">
        <template #header><div class="card-header"><span>原始查询结果</span><span class="result-meta">{{ result.source_name }} / {{ result.schema }} / {{ result.rows?.length || 0 }} 行</span></div></template>
        <el-table :data="result.rows || []" border stripe size="small" max-height="560"><el-table-column v-for="column in result.columns || []" :key="column" :prop="column" :label="column" min-width="150" show-overflow-tooltip /></el-table>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { domainApi, sourceApi } from '../../api'
import { useAppStore } from '../../stores/app'

const appStore = useAppStore()
const domainId = ref(appStore.currentDomainId || '')
const sourceId = ref('')
const schema = ref('')
const rowLimit = ref(200)
const domains = ref<any[]>([])
const sources = ref<any[]>([])
const schemas = ref<string[]>([])
const result = ref<any>(null)
const executing = ref(false)
const graphSql = ref(`SELECT
  a.element_id AS source_id,
  a.element_id AS source_label,
  b.element_id AS target_id,
  b.element_id AS target_label,
  e.label AS relation_name
FROM GRAPH_TABLE (
  <PROPERTY_GRAPH_NAME>
  MATCH (a)-[e]->(b)
  COLUMNS (a.element_id, b.element_id, e.label)
)`)

const canExecute = computed(() => Boolean(domainId.value && sourceId.value && graphSql.value.trim()))
const rowValue = (row: any, names: string[]) => { const key = Object.keys(row || {}).find(item => names.includes(item.toUpperCase())); return key ? String(row[key] ?? '') : '' }
const graphData = computed(() => {
  const nodeMap = new Map<string, any>(); const edges: any[] = []
  ;(result.value?.rows || []).forEach((row: any, index: number) => {
    const source = rowValue(row, ['SOURCE_ID', 'SOURCE', 'FROM_ID']); const target = rowValue(row, ['TARGET_ID', 'TARGET', 'TO_ID'])
    if (!source || !target) return
    const sourceLabel = rowValue(row, ['SOURCE_LABEL', 'SOURCE_NAME', 'FROM_LABEL']) || source
    const targetLabel = rowValue(row, ['TARGET_LABEL', 'TARGET_NAME', 'TO_LABEL']) || target
    nodeMap.set(source, { id: source, label: sourceLabel }); nodeMap.set(target, { id: target, label: targetLabel })
    edges.push({ id: `${source}-${target}-${index}`, source, target, label: rowValue(row, ['RELATION_NAME', 'EDGE_LABEL', 'LABEL', 'RELATION']) || '关联' })
  })
  return { nodes: Array.from(nodeMap.values()).slice(0, 80), edges: edges.slice(0, 160) }
})
const nodePosition = (id: string) => { const index = graphData.value.nodes.findIndex((node: any) => node.id === id); const count = Math.max(graphData.value.nodes.length, 1); const angle = (Math.PI * 2 * index) / count - Math.PI / 2; const radius = Math.min(230, Math.max(100, count * 11)); return { x: 550 + radius * Math.cos(angle), y: 310 + radius * Math.sin(angle) } }
const loadDomains = async () => { try { const res = await domainApi.list('ACTIVE'); domains.value = res.data || [] } catch (_) {} }
const loadSources = async () => { if (!domainId.value) return; try { const res = await sourceApi.listDataSources(domainId.value); sources.value = (res.data || []).filter((item: any) => (item.db_type || '').toLowerCase() === 'oracle'); sourceId.value = sources.value.find((item: any) => item.is_default === 'Y')?.source_id || sources.value[0]?.source_id || ''; await loadSchemas() } catch (_) { sources.value = [] } }
const loadSchemas = async () => { if (!sourceId.value) { schemas.value = []; return }; try { const res = await sourceApi.getSchemas(sourceId.value); schemas.value = res.data?.schemas || []; schema.value = res.data?.default_schema || schemas.value[0] || '' } catch (_) { schemas.value = [] } }
const handleDomainChange = async () => { const domain = domains.value.find(item => item.domain_id === domainId.value); appStore.setCurrentDomain(domainId.value, domain?.domain_name || ''); result.value = null; sourceId.value = ''; schema.value = ''; await loadSources() }
const handleSourceChange = async () => { result.value = null; await loadSchemas() }
const executeQuery = async () => { if (!canExecute.value) return; executing.value = true; try { const res = await sourceApi.executeGraphQuery({ domain_id: domainId.value, source_id: sourceId.value, schema: schema.value || undefined, graph_sql: graphSql.value, row_limit: rowLimit.value }); result.value = res.data; ElMessage.success(`查询完成，返回 ${res.data?.rows?.length || 0} 行`) } catch (_) {} finally { executing.value = false } }
onMounted(async () => { await loadDomains(); if (domainId.value) await loadSources() })
</script>

<style scoped>
.graph-query-page { min-height: calc(100vh - 86px); padding: 8px 0 20px; }.page-header { margin: 8px 0 16px; }.eyebrow { color: #2563eb; font-size: 11px; font-weight: 800; letter-spacing: .14em; }.page-header h2 { margin: 4px 0; color: #0f172a; font-size: 25px; }.page-header p { margin: 0; color: #64748b; font-size: 13px; }.query-card, .graph-result-card, .table-result-card { border-color: #e4eaf2; }.query-form { display: grid; grid-template-columns: 1.2fr 1.3fr 1fr 105px auto; gap: 10px; }.form-hint { margin: 10px 0; color: #64748b; font-size: 12px; }.form-hint code { color: #2563eb; }.sql-editor :deep(textarea) { font-family: 'SFMono-Regular', Consolas, monospace; font-size: 13px; }.result-alert { margin: 16px 0; }.result-layout { display: grid; grid-template-columns: 1.05fr 1fr; gap: 16px; }.card-header { display: flex; justify-content: space-between; align-items: center; gap: 10px; }.result-meta { color: #64748b; font-size: 12px; }.graph-canvas { height: 590px; overflow: hidden; background: radial-gradient(circle at 1px 1px, #d6e0eb 1px, transparent 1.2px); background-size: 22px 22px; }.graph-svg { width: 100%; height: 100%; }.edge-line { stroke: #94a3b8; stroke-width: 1.7; }.edge-text { fill: #475569; font-size: 11px; text-anchor: middle; }.query-node circle { fill: #eff6ff; stroke: #2563eb; stroke-width: 2; }.query-node text { fill: #1e3a5f; font-size: 10px; text-anchor: middle; pointer-events: none; }.table-result-card { min-width: 0; } @media (max-width: 1200px) { .query-form, .result-layout { grid-template-columns: 1fr; }.graph-canvas { height: 460px; } }
</style>
