<template>
  <div class="browse-page">
    <section class="browse-header">
      <div>
        <div class="eyebrow">ONTOLOGY GRAPH</div>
        <h2>本体图谱浏览</h2>
        <p>图中的顶点和关系直接读取自当前业务分析域下所选 Oracle 数据源的 Property Graph 视图。</p>
      </div>
      <div class="header-actions">
        <el-select v-model="selectedSourceId" placeholder="选择 Oracle 数据源" filterable @change="handleSourceChange">
          <el-option v-for="source in sourceDataSources" :key="source.source_id" :label="`${source.source_name} / ${source.schema_name || source.username}`" :value="source.source_id" />
        </el-select>
        <el-select v-model="selectedGraphName" placeholder="选择 Property Graph" :disabled="!selectedSourceId || !availableGraphs.length" filterable @change="loadGraphData">
          <el-option v-for="graph in availableGraphs" :key="graph.graph_name" :label="graph.graph_name" :value="graph.graph_name" />
        </el-select>
        <el-button :icon="Refresh" :disabled="!selectedSourceId" @click="loadGraphData">刷新</el-button>
      </div>
    </section>

    <el-alert v-if="graphDeploymentWarning" type="warning" :closable="false" show-icon class="deployment-warning" :title="graphDeploymentWarning" />

    <main class="browse-layout">
      <section class="graph-card">
        <div class="graph-toolbar">
          <div class="graph-summary">
            <span><b>{{ graphNodes.length }}</b> 个本体对象</span><i></i><span><b>{{ graphEdges.length }}</b> 条关系</span>
          </div>
          <div class="graph-tools">
            <el-button size="small" :icon="FullScreen" :disabled="!graphNodes.length" @click="fitView">适应画布</el-button>
            <el-button size="small" :disabled="!graphNodes.length" @click="resetLayout">自动布局</el-button>
          </div>
        </div>

        <div v-if="graphNodes.length" ref="graphCanvasRef" class="graph-canvas"></div>
        <el-empty v-else description="请选择包含 Property Graph 的 Oracle 数据源" :image-size="96" />
        <div v-if="graphNodes.length" class="canvas-tip">拖动节点调整位置 · 滚轮缩放 · 空白处平移</div>
        <div v-if="graphNodes.length" class="legend">
          <span><i class="legend-dot table"></i>管理表</span>
          <span><i class="legend-dot view"></i>管理视图</span>
          <span><i class="legend-line"></i>有向本体关系</span>
        </div>
      </section>

      <aside class="detail-panel">
        <template v-if="selectedNode">
          <div class="detail-heading">
            <span class="detail-color" :style="{ background: nodeColor(selectedNode) }"></span>
            <div><h3>{{ selectedNode.displayName || selectedNode.name }}</h3><code>{{ selectedNode.technicalName || selectedNode.name }}</code></div>
          </div>
          <div class="detail-tags">
            <el-tag effect="plain">{{ selectedNode.buildType }}</el-tag>
            <el-tag :type="selectedNode.status === 'DEPLOYED' ? 'success' : 'warning'">{{ selectedNode.status }}</el-tag>
          </div>
          <dl class="detail-info">
            <div><dt>对象表</dt><dd><code>{{ selectedNode.tableName || '待生成' }}</code></dd></div>
            <div><dt>本体名称</dt><dd>{{ selectedNode.entityName || selectedNode.name }}</dd></div>
            <div><dt>关系数量</dt><dd>{{ connectedRelationCount }}</dd></div>
            <div class="full"><dt>业务描述</dt><dd>{{ selectedNode.desc || '暂无描述' }}</dd></div>
          </dl>
          <div class="property-header"><h4>目标数据库中的实际属性</h4></div>
          <el-table :data="selectedNode.properties" size="small" max-height="360" class="property-table">
            <el-table-column prop="property_display_name" label="属性"><template #default="{ row }"><div>{{ row.property_display_name || row.property_name }}</div><code>{{ row.property_name }}</code></template></el-table-column>
            <el-table-column prop="data_type" label="类型" width="86" />
            <el-table-column label="标记" width="65"><template #default="{ row }"><el-tag v-if="row.is_primary_key === 'Y'" type="danger" size="small">PK</el-tag></template></el-table-column>
          </el-table>
        </template>
        <el-empty v-else description="选择图中的节点，查看对象详情" :image-size="78" />
      </aside>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import cytoscape from 'cytoscape'
import { FullScreen, Refresh } from '@element-plus/icons-vue'
import { graphApi, sourceApi } from '../../api'
import { useAppStore } from '../../stores/app'

const appStore = useAppStore()
const currentDomainId = computed(() => appStore.currentDomainId)
const selectedSourceId = ref('')
const selectedGraphName = ref('')
const sourceDataSources = ref<any[]>([])
const availableGraphs = ref<any[]>([])
const graphNodes = ref<any[]>([])
const graphEdges = ref<any[]>([])
const selectedNode = ref<any>(null)
const graphDeploymentWarning = ref('')
const graphCanvasRef = ref<HTMLElement | null>(null)
let graphCy: cytoscape.Core | null = null

const connectedRelationCount = computed(() => graphEdges.value.filter(edge => edge.source === selectedNode.value?.id || edge.target === selectedNode.value?.id).length)
const truncate = (value: string, length: number) => value && value.length > length ? `${value.slice(0, length)}…` : value
const nodeColor = (node: any) => node.color || (node.buildType === 'VIEW' ? '#0ea5e9' : '#10b981')

const loadSourceDataSources = async () => {
  try {
    const res = await sourceApi.listDataSources(currentDomainId.value || undefined)
    sourceDataSources.value = (res.data || []).filter((source: any) => (source.db_type || '').toLowerCase() === 'oracle')
    if (!sourceDataSources.value.some(source => source.source_id === selectedSourceId.value)) selectedSourceId.value = sourceDataSources.value.find(source => source.is_default === 'Y')?.source_id || ''
  } catch (_) { sourceDataSources.value = []; selectedSourceId.value = '' }
}

const renderGraph = async () => {
  await nextTick()
  if (!graphCanvasRef.value || !graphNodes.value.length) return
  graphCy?.destroy()
  graphCy = cytoscape({
    container: graphCanvasRef.value,
    elements: [
      ...graphNodes.value.map(node => ({ data: { id: node.id, label: truncate(node.displayName || node.name, 12), color: nodeColor(node), type: node.buildType === 'VIEW' ? '管理视图' : '管理表' }, classes: selectedNode.value?.id === node.id ? 'selected' : '' })),
      ...graphEdges.value.map(edge => ({ data: { id: edge.id, source: edge.source, target: edge.target, label: edge.name || '关联' } }))
    ],
    style: [
      { selector: 'node', style: { shape: 'ellipse', width: 82, height: 82, label: 'data(label)', color: '#fff', 'font-size': 12, 'font-weight': 'bold', 'text-wrap': 'wrap', 'text-max-width': '68px', 'text-valign': 'center', 'text-halign': 'center', 'background-color': 'data(color)', 'border-width': 2, 'border-color': '#fff' } },
      { selector: 'node.selected', style: { width: 92, height: 92, 'border-width': 5, 'border-color': '#1d4ed8' } },
      { selector: 'edge', style: { width: 1.8, 'line-color': '#94a3b8', 'target-arrow-color': '#94a3b8', 'target-arrow-shape': 'triangle', 'curve-style': 'bezier', label: 'data(label)', color: '#475569', 'font-size': 10, 'font-weight': 'bold', 'text-background-color': '#fff', 'text-background-opacity': .92, 'text-background-padding': '4px', 'text-background-shape': 'roundrectangle', 'text-rotation': 'autorotate' } },
      { selector: 'node:active', style: { 'overlay-opacity': 0 } },
      { selector: 'node:selected, node.highlighted', style: { 'border-color': '#1d4ed8' } },
      { selector: 'edge:active, edge:selected', style: { width: 3, 'line-color': '#2563eb', 'target-arrow-color': '#2563eb' } }
    ],
    layout: { name: 'breadthfirst', directed: true, padding: 80, spacingFactor: 1.32, animate: true, animationDuration: 450, circle: false }
  })
  graphCy.on('tap', 'node', event => {
    const id = event.target.id()
    graphCy?.nodes().removeClass('selected')
    event.target.addClass('selected')
    selectedNode.value = graphNodes.value.find(node => node.id === id) || null
  })
}
const fitView = () => graphCy?.fit(undefined, 64)
const resetLayout = () => graphCy?.layout({ name: 'breadthfirst', directed: true, padding: 80, spacingFactor: 1.32, animate: true, animationDuration: 450, circle: false }).run()
const resizeGraph = () => { graphCy?.resize(); graphCy?.fit(undefined, 64) }

const loadGraphData = async () => {
  if (!currentDomainId.value || !selectedSourceId.value) { graphNodes.value = []; graphEdges.value = []; availableGraphs.value = []; graphCy?.destroy(); graphCy = null; return }
  try {
    const res = await graphApi.getOntologyBrowseGraph(selectedSourceId.value, selectedGraphName.value, currentDomainId.value)
    availableGraphs.value = res.data?.graphs || []
    if (!availableGraphs.value.some((graph: any) => graph.graph_name === selectedGraphName.value)) selectedGraphName.value = res.data?.graph_name || ''
    graphDeploymentWarning.value = ''
    graphNodes.value = res.data?.nodes || []
    graphEdges.value = res.data?.edges || []
    selectedNode.value = graphNodes.value.find((node: any) => node.id === selectedNode.value?.id) || null
    void renderGraph()
  } catch (_) { graphDeploymentWarning.value = '' }
}

const handleSourceChange = () => { selectedNode.value = null; selectedGraphName.value = ''; availableGraphs.value = []; void loadGraphData() }
watch(() => appStore.currentDomainId, async () => { selectedSourceId.value = ''; selectedGraphName.value = ''; availableGraphs.value = []; graphNodes.value = []; graphEdges.value = []; selectedNode.value = null; await loadSourceDataSources(); if (selectedSourceId.value) await loadGraphData() })
onMounted(async () => { window.addEventListener('resize', resizeGraph); await loadSourceDataSources(); if (selectedSourceId.value) await loadGraphData() })
onBeforeUnmount(() => { window.removeEventListener('resize', resizeGraph); graphCy?.destroy(); graphCy = null })
</script>

<style scoped>
.browse-page { min-height: calc(100vh - 86px); padding: 4px 0 18px; color: #1e293b; }.browse-header { display: flex; justify-content: space-between; align-items: flex-end; gap: 20px; padding: 12px 4px 18px; }.eyebrow { color: #2563eb; font-size: 11px; font-weight: 800; letter-spacing: .14em; }.browse-header h2 { margin: 5px 0 4px; font-size: 25px; letter-spacing: -.02em; }.browse-header p { margin: 0; color: #64748b; font-size: 13px; }.header-actions { display: flex; gap: 10px; align-items: center; }.header-actions .el-select { width: 230px; }.header-actions .el-select + .el-select { width: 270px; }.deployment-warning { margin: 0 4px 16px; }
.browse-layout { display: grid; grid-template-columns: minmax(0, 1fr) 350px; gap: 16px; min-height: calc(100vh - 175px); }.graph-card, .detail-panel { background: #fff; border: 1px solid #e5eaf1; border-radius: 16px; box-shadow: 0 8px 28px rgba(15, 23, 42, .05); overflow: hidden; }.graph-card { position: relative; display: flex; flex-direction: column; min-height: 590px; }.graph-toolbar { display: flex; justify-content: space-between; align-items: center; min-height: 54px; padding: 0 16px; border-bottom: 1px solid #edf1f6; }.graph-summary { display: flex; align-items: center; gap: 9px; color: #64748b; font-size: 13px; }.graph-summary b { color: #0f172a; }.graph-summary i { width: 1px; height: 13px; background: #d6dee9; }.graph-tools { display: flex; align-items: center; gap: 6px; }
.graph-canvas { flex: 1; min-height: 530px; background: radial-gradient(circle at 1px 1px, #d6e0eb 1px, transparent 1.2px), radial-gradient(circle at 50% 0%, #fff 0, #f4f8fc 100%); background-size: 22px 22px, auto; }.canvas-tip { position: absolute; left: 16px; bottom: 14px; padding: 7px 10px; border: 1px solid #e2e8f0; border-radius: 8px; background: rgba(255,255,255,.92); color: #64748b; font-size: 11px; pointer-events: none; }.legend { position: absolute; right: 16px; bottom: 14px; display: flex; gap: 12px; padding: 7px 10px; border: 1px solid #e2e8f0; border-radius: 8px; background: rgba(255,255,255,.92); color: #64748b; font-size: 11px; pointer-events: none; }.legend span { display: flex; align-items: center; gap: 5px; }.legend-dot { width: 8px; height: 8px; border-radius: 50%; }.legend-dot.table { background: #10b981; }.legend-dot.view { background: #0ea5e9; }.legend-line { width: 18px; height: 2px; background: #94a3b8; position: relative; }.legend-line::after { content: ''; position: absolute; right: -1px; top: -3px; border: 4px solid transparent; border-left-color: #94a3b8; }
.detail-panel { padding: 20px; overflow: auto; }.detail-heading { display: flex; align-items: center; gap: 10px; }.detail-color { width: 12px; height: 42px; border-radius: 8px; }.detail-heading h3 { margin: 0 0 3px; font-size: 18px; }.detail-heading code, .property-table code { color: #64748b; font-size: 11px; }.detail-tags { display: flex; gap: 7px; margin: 16px 0; }.detail-info { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 0 0 20px; }.detail-info div { padding: 10px; background: #f8fafc; border-radius: 9px; }.detail-info .full { grid-column: 1 / -1; }.detail-info dt { color: #94a3b8; font-size: 11px; }.detail-info dd { margin: 4px 0 0; color: #334155; font-size: 12px; line-height: 1.55; word-break: break-word; }.property-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }.property-header h4 { margin: 0; font-size: 15px; }.property-table { border-top: 1px solid #edf1f6; }
@media (max-width: 1100px) { .browse-layout { grid-template-columns: 1fr; }.detail-panel { min-height: 260px; }.browse-header { align-items: flex-start; flex-direction: column; }.header-actions { width: 100%; }.header-actions .el-select { flex: 1; } }
</style>
