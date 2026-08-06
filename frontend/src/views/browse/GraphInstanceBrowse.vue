<template>
  <div class="instance-page">
    <section class="page-header">
      <div><div class="eyebrow">PROPERTY GRAPH INSTANCES</div><h2>实例数据展示</h2><p>当前业务分析域：{{ appStore.currentDomainName || '未选择' }}。选择本体节点并按属性条件查询，结果以实例子图展示。</p></div>
    </section>
    <el-alert v-if="!currentDomainId" type="warning" :closable="false" show-icon title="请先在页面顶部选择业务分析域" />
    <el-card v-else shadow="never" class="query-card">
      <div class="query-grid">
        <el-select v-model="sourceId" placeholder="选择当前业务域数据源" @change="handleSourceChange"><el-option v-for="source in sources" :key="source.source_id" :label="`${source.source_name} / ${source.schema_name || source.username}`" :value="source.source_id" /></el-select>
        <el-select v-model="graphName" placeholder="选择 Property Graph" :disabled="!sourceId" @change="loadTopology"><el-option v-for="graph in graphs" :key="graph.graph_name" :label="graph.graph_name" :value="graph.graph_name" /></el-select>
        <el-select v-model="nodeId" placeholder="选择本体节点" :disabled="!graphName" @change="resetCondition"><el-option v-for="node in topologyNodes" :key="node.id" :label="node.displayName || node.name" :value="node.id" /></el-select>
        <el-select v-model="propertyName" placeholder="选择条件属性（可选）" :disabled="!selectedNode"><el-option v-for="property in selectedNode?.properties || []" :key="property.property_name" :label="property.property_display_name || property.property_name" :value="property.property_name" /></el-select>
        <el-select v-model="operator" :disabled="!propertyName" class="operator"><el-option label="包含" value="contains" /><el-option label="等于" value="equals" /><el-option label="大于" value="greater_than" /><el-option label="小于" value="less_than" /></el-select>
        <el-input v-model="conditionValue" :disabled="!propertyName" placeholder="输入查询条件" @keyup.enter="queryInstances" />
        <el-input-number v-model="rowLimit" :min="1" :max="100" controls-position="right" />
        <el-button type="primary" :disabled="!nodeId" :loading="loading" @click="queryInstances">查询实例</el-button>
      </div>
      <div class="hint">不填条件属性时，返回所选节点前 {{ rowLimit }} 条实例；已命中的实例会展示其一跳关联实例。</div>
    </el-card>
    <el-alert v-if="result" :type="result.rows?.length ? 'success' : 'info'" :closable="false" show-icon class="result-summary" :title="`查询到 ${result.rows?.length || 0} 个 ${selectedNode?.displayName || '节点'} 实例，以及 ${(result.edges || []).length} 条一跳关系`" />
    <div v-if="result" class="result-grid">
      <el-card shadow="never" class="graph-card"><template #header><span>{{ lineageActive ? '上下游全链路实例图' : '实例子图' }}</span></template>
        <div class="graph-canvas"><svg v-if="result.nodes?.length" viewBox="0 0 1000 600"><defs><marker id="instance-arrow" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto"><path d="M0,0 L9,4.5 L0,9 z" fill="#64748b" /></marker></defs><g v-for="edge in result.edges" :key="edge.id"><line :x1="pos(edge.source).x" :y1="pos(edge.source).y" :x2="pos(edge.target).x" :y2="pos(edge.target).y" class="edge" marker-end="url(#instance-arrow)" /><text :x="(pos(edge.source).x+pos(edge.target).x)/2" :y="(pos(edge.source).y+pos(edge.target).y)/2-8" class="edge-label">{{ edge.label }}</text></g><g v-for="node in result.nodes" :key="node.id" :transform="`translate(${pos(node.id).x},${pos(node.id).y})`"><rect x="-70" y="-31" width="140" height="62" rx="12" :class="node.selected ? 'selected-node' : 'neighbor-node'" /><text y="-5" class="node-type">{{ trim(node.label, 13) }}</text><text y="16" class="node-value">{{ trim(node.instance_label, 16) }}</text></g></svg><el-empty v-else description="没有匹配的实例数据" /></div>
      </el-card>
      <el-card shadow="never" class="table-card"><template #header><span>命中实例数据（点击记录查看全链路）</span></template><el-table :data="result.rows || []" border stripe size="small" max-height="560" highlight-current-row @row-click="showLineage"><el-table-column v-for="column in resultColumns" :key="column" :prop="column" :label="column" min-width="140" show-overflow-tooltip /></el-table></el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { graphApi, sourceApi } from '../../api'
import { useAppStore } from '../../stores/app'
const appStore = useAppStore(); const currentDomainId = computed(() => appStore.currentDomainId)
const sources = ref<any[]>([]); const graphs = ref<any[]>([]); const topologyNodes = ref<any[]>([]); const topologyEdges = ref<any[]>([]); const sourceId = ref(''); const graphName = ref(''); const nodeId = ref(''); const propertyName = ref(''); const operator = ref('contains'); const conditionValue = ref(''); const rowLimit = ref(30); const result = ref<any>(null); const loading = ref(false); const lineageActive = ref(false)
const selectedNode = computed(() => topologyNodes.value.find(node => node.id === nodeId.value)); const resultColumns = computed(() => Object.keys(result.value?.rows?.[0] || {}))
const trim = (value: any, size: number) => { const text = String(value ?? ''); return text.length > size ? `${text.slice(0, size)}…` : text }
const pos = (id: string) => { const nodes = result.value?.nodes || []; const index = nodes.findIndex((node: any) => node.id === id); const selected = nodes.filter((node: any) => node.selected); const node = nodes[index]; if (node?.selected) { const i = selected.findIndex((item: any) => item.id === id); const angle = selected.length > 1 ? Math.PI * 2 * i / selected.length : 0; return { x: 500 + Math.cos(angle) * Math.min(210, selected.length * 30), y: 300 + Math.sin(angle) * Math.min(150, selected.length * 24) } } const neighbors = nodes.filter((node: any) => !node.selected); const i = neighbors.findIndex((item: any) => item.id === id); const angle = neighbors.length > 1 ? Math.PI * 2 * i / neighbors.length : 0; return { x: 500 + Math.cos(angle) * 390, y: 300 + Math.sin(angle) * 230 } }
const resetCondition = () => { propertyName.value = ''; conditionValue.value = ''; result.value = null; lineageActive.value = false }
const loadSources = async () => { if (!currentDomainId.value) { sources.value=[]; return }; const res = await sourceApi.listDataSources(currentDomainId.value); sources.value=(res.data||[]).filter((item:any)=>(item.db_type||'').toLowerCase()==='oracle'); sourceId.value=sources.value.find((item:any)=>item.is_default==='Y')?.source_id || sources.value[0]?.source_id || ''; graphName.value=''; await loadTopology() }
const handleSourceChange = async () => { graphName.value=''; await loadTopology() }
const loadTopology = async () => { graphs.value=[]; topologyNodes.value=[]; topologyEdges.value=[]; nodeId.value=''; result.value=null; if (!sourceId.value || !currentDomainId.value) return; const res:any=await graphApi.getOntologyBrowseGraph(sourceId.value, graphName.value || undefined, currentDomainId.value); graphs.value=res.data?.graphs||[]; graphName.value=res.data?.graph_name||''; topologyNodes.value=res.data?.nodes||[]; topologyEdges.value=res.data?.edges||[] }
const applyChineseLabels = (data:any) => { data.nodes=(data.nodes||[]).map((node:any)=>({...node,label:topologyNodes.value.find((item:any)=>item.id===node.node_id)?.displayName||node.label})); data.edges=(data.edges||[]).map((edge:any)=>({...edge,label:topologyEdges.value.find((item:any)=>item.id===edge.edge_id)?.name||edge.label})); return data }
const queryInstances = async () => { loading.value=true; try { const res:any=await graphApi.queryOntologyGraphInstances({ domain_id: currentDomainId.value, source_id: sourceId.value, graph_name: graphName.value, node_id: nodeId.value, property_name: propertyName.value || null, operator: operator.value, value: conditionValue.value || null, row_limit: rowLimit.value }); result.value=applyChineseLabels(res.data||{}); lineageActive.value=false } finally { loading.value=false } }
const showLineage = async (row:any) => { const key=selectedNode.value?.properties?.find((item:any)=>item.is_primary_key==='Y')?.property_name; if (!key || row[key] == null) return; loading.value=true; try { const res:any=await graphApi.queryOntologyGraphInstanceLineage({domain_id:currentDomainId.value,source_id:sourceId.value,graph_name:graphName.value,node_id:nodeId.value,instance_key:String(row[key]),max_depth:12}); const lineage=applyChineseLabels(res.data||{}); lineage.rows=result.value?.rows||[]; result.value=lineage; lineageActive.value=true } finally { loading.value=false } }
watch(() => appStore.currentDomainId, loadSources); onMounted(loadSources)
</script>

<style scoped>
.instance-page{min-height:calc(100vh - 86px);padding:8px 0 20px}.page-header{margin:8px 0 16px}.eyebrow{color:#2563eb;font-size:11px;font-weight:800;letter-spacing:.14em}.page-header h2{margin:4px 0;color:#0f172a;font-size:25px}.page-header p,.hint{margin:0;color:#64748b;font-size:13px}.query-card,.graph-card,.table-card{border-color:#e4eaf2}.query-grid{display:grid;grid-template-columns:1.35fr 1.2fr 1.2fr 1.1fr .7fr 1fr 105px auto;gap:10px}.hint{margin-top:12px}.result-summary{margin:16px 0}.result-grid{display:grid;grid-template-columns:1.1fr 1fr;gap:16px}.graph-canvas{height:590px;overflow:hidden;background:radial-gradient(circle at 1px 1px,#d6e0eb 1px,transparent 1.2px);background-size:22px 22px}.graph-canvas svg{width:100%;height:100%}.edge{stroke:#94a3b8;stroke-width:1.6}.edge-label{font-size:11px;fill:#475569;text-anchor:middle}.selected-node{fill:#dbeafe;stroke:#2563eb;stroke-width:2}.neighbor-node{fill:#f8fafc;stroke:#94a3b8;stroke-width:1.5}.node-type{font-size:11px;fill:#1e3a5f;text-anchor:middle;font-weight:700}.node-value{font-size:10px;fill:#475569;text-anchor:middle}.table-card{min-width:0}@media(max-width:1280px){.query-grid,.result-grid{grid-template-columns:1fr}.graph-canvas{height:460px}}
</style>
