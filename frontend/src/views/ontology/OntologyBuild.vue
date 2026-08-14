<template>
  <div class="ontology-build-page">
    <div class="top-bar" :class="{ 'top-bar-end': !fixedBuildSection }">
      <div v-if="!fixedBuildSection" class="tab-section">
        <el-radio-group v-model="activeTab">
          <el-radio-button value="graph">本体关系图</el-radio-button>
          <el-radio-button value="flow">分析流程图</el-radio-button>
        </el-radio-group>
      </div>
    </div>

    <!-- ==================== Ontology Graph Tab ==================== -->
    <div v-if="activeTab === 'graph'" class="graph-container">
      <div class="toolbar">
        <el-button type="primary" size="small" @click="showAddEntity">添加实体</el-button>
        <el-button type="success" size="small" @click="showAddRelation">添加关系</el-button>
        <el-button type="primary" plain size="small" @click="openOntologyGuide">Guide自动生成</el-button>
        <el-button type="warning" plain size="small" @click="openNaturalAdjustDialog">自然语言调整</el-button>
        <el-button type="danger" plain size="small" @click="clearOntologyData">清空本体</el-button>
        <el-button size="small" @click="loadGraphData">刷新</el-button>
        <el-button type="warning" size="small" @click="saveAllPositions">保存位置</el-button>
        <span class="drag-hint">💡 单击实体查看属性并显示连线锚点，拖拽锚点到目标实体可快速创建关系，双击关系边可编辑或删除</span>
      </div>
      <div class="graph-layout">
        <!-- SVG Canvas -->
        <div class="graph-area" ref="graphAreaRef">
          <div v-if="graphNodes.length > 0" class="svg-graph">
            <svg
              ref="graphSvgRef"
              :width="canvasSize.w"
              :height="graphCanvasHeight"
              :viewBox="`0 0 ${canvasSize.w} ${graphCanvasHeight}`"
              @mousemove="onGraphMouseMove"
              @mouseup="onGraphMouseUp"
              @mouseleave="onGraphMouseUp"
              style="cursor: default"
            >
              <defs>
                <marker id="graphArrowhead" markerWidth="10" markerHeight="7" refX="10" refY="3.5" orient="auto">
                  <polygon points="0 0, 10 3.5, 0 7" fill="#888" />
                </marker>
                <marker id="graphPreviewArrowhead" markerWidth="10" markerHeight="7" refX="10" refY="3.5" orient="auto">
                  <polygon points="0 0, 10 3.5, 0 7" fill="#409EFF" />
                </marker>
              </defs>
              <!-- Edges -->
              <g
                v-for="edge in graphEdges"
                :key="edge.id"
                class="graph-edge"
                :class="{
                  'is-related': isRelatedGraphEdge(edge),
                  'is-muted': selectedNode && !isRelatedGraphEdge(edge)
                }"
                @dblclick.stop="openRelationEditor(edge)"
              >
                <line
                  :x1="getGraphEdgeGeometry(edge).start.x"
                  :y1="getGraphEdgeGeometry(edge).start.y"
                  :x2="getGraphEdgeGeometry(edge).end.x"
                  :y2="getGraphEdgeGeometry(edge).end.y"
                  stroke="#888" stroke-width="2" marker-end="url(#graphArrowhead)"
                />
                <rect
                  :x="getGraphEdgeGeometry(edge).mid.x - (edge.name.length * 14 + 16) / 2"
                  :y="getGraphEdgeGeometry(edge).mid.y - 10"
                  :width="edge.name.length * 14 + 16"
                  height="20" rx="4" fill="#fff" stroke="#ccc" stroke-width="1"
                />
                <text
                  :x="getGraphEdgeGeometry(edge).mid.x"
                  :y="getGraphEdgeGeometry(edge).mid.y + 4"
                  text-anchor="middle" fill="#555" font-size="11"
                >{{ edge.name }}</text>
              </g>
              <line
                v-if="graphConnecting"
                :x1="graphConnecting.startX"
                :y1="graphConnecting.startY"
                :x2="graphConnecting.mouseX"
                :y2="graphConnecting.mouseY"
                stroke="#409EFF"
                stroke-width="2.5"
                stroke-dasharray="6,4"
                marker-end="url(#graphPreviewArrowhead)"
              />
              <!-- Nodes -->
              <g
                v-for="node in graphNodes"
                :key="node.id"
                :transform="`translate(${getNodePos(node.id).x}, ${getNodePos(node.id).y})`"
                class="graph-node"
                :class="{
                  'is-selected': selectedNode?.id === node.id,
                  'is-related': isRelatedGraphNode(node.id),
                  'is-muted': selectedNode && !isRelatedGraphNode(node.id) && selectedNode?.id !== node.id
                }"
                @mousedown.stop="onNodeMouseDown($event, node)"
                @click.stop="onNodeClick(node)"
                @dblclick.stop="openEntityEditor(node)"
              >
                <rect
                  :width="160" :height="70" :rx="10" :ry="10"
                  :fill="node.color || (node.buildType === 'VIEW' ? '#4fc3f7' : '#66bb6a')"
                  :stroke="draggingNode?.id === node.id ? '#409EFF' : '#333'"
                  :stroke-width="draggingNode?.id === node.id ? 3 : 2"
                  style="cursor: grab"
                />
                <text x="80" y="22" text-anchor="middle" fill="#fff" font-size="13" font-weight="bold">{{ node.displayName || node.name }}</text>
                <text x="80" y="40" text-anchor="middle" fill="rgba(255,255,255,0.9)" font-size="11">{{ node.name }}</text>
                <text x="80" y="58" text-anchor="middle" fill="rgba(255,255,255,0.7)" font-size="9">{{ node.buildType }} | 属性:{{ node.propertiesCount || 0 }}</text>
                <g
                  v-if="selectedNode?.id === node.id"
                  class="graph-connector"
                  @mousedown.stop="startGraphConnection($event, node)"
                >
                  <circle cx="160" cy="35" r="10" fill="#409EFF" stroke="#ffffff" stroke-width="2" />
                  <text x="160" y="39" text-anchor="middle" fill="#fff" font-size="11">+</text>
                </g>
              </g>
            </svg>
          </div>
          <el-empty v-else description="请先选择业务分析域并添加实体">
            <el-button type="primary" @click="showAddEntity">添加第一个实体</el-button>
          </el-empty>
        </div>
        <!-- Property Panel -->
        <div class="property-panel" v-if="selectedNode">
          <h4>
            <span :style="{color: selectedNode.color || '#66bb6a'}">●</span>
            {{ selectedNode.displayName || selectedNode.name }}
          </h4>
          <div class="entity-info">
            <p><strong>实体名称:</strong> {{ selectedNode.name }}</p>
            <p><strong>构建方式:</strong> TABLE/VIEW</p>
            <p><strong>状态:</strong>
              <el-tag :type="selectedNode.status === 'DEPLOYED' ? 'success' : 'info'" size="small">{{ selectedNode.status }}</el-tag>
            </p>
            <p><strong>表名:</strong> <code>{{ selectedNode.tableName || '-' }}</code></p>
            <p><strong>描述:</strong> {{ selectedNode.desc || '无' }}</p>
          </div>
          <div class="property-list">
            <div class="property-list-header">
              <h5>属性列表</h5>
              <el-button type="primary" size="small" @click="showAddProperty">+ 添加属性</el-button>
            </div>
            <div v-for="prop in selectedNodeProperties" :key="prop.property_id" class="prop-item">
              <span class="prop-name">{{ prop.property_name }}</span>
              <span class="prop-type">{{ prop.data_type }}</span>
              <el-tag v-if="prop.is_primary_key === 'Y'" type="danger" size="small">PK</el-tag>
              <span class="prop-desc">{{ prop.property_display_name || prop.property_desc }}</span>
              <el-button size="small" type="primary" link @click="showEditProperty(prop)">修改</el-button>
              <el-popconfirm title="确定删除此属性?" @confirm="deleteProperty(prop.property_id)">
                <template #reference>
                  <el-button size="small" type="danger" :icon="Delete" circle />
                </template>
              </el-popconfirm>
            </div>
          </div>
          <el-button type="primary" size="small" plain style="margin-top:12px;width:100%" @click="openEntityEditor(selectedNode)">编辑实体</el-button>
          <el-button type="danger" size="small" plain style="margin-top:12px;width:100%" @click="deleteEntity">删除实体</el-button>
        </div>
      </div>
    </div>

    <!-- ==================== Flow Tab ==================== -->
    <div v-if="activeTab === 'flow'" class="flow-container">
      <div class="flow-overview-card">
        <div class="flow-overview-main">
          <div class="flow-overview-title">分析流程可视化创建</div>
          <p class="flow-overview-desc">
            以流程图方式描述分析逐步开展的过程。先创建一个分析流程图，再通过拖拽节点、节点连线和参数配置，逐步定义数据输入、分析判断和输出动作。
          </p>
          <div class="flow-overview-steps">
            <span class="flow-step">1. 新建流程图</span>
            <span class="flow-step">2. 拖拽节点到画布</span>
            <span class="flow-step">3. 连接流程步骤</span>
            <span class="flow-step">4. 配置节点含义</span>
            <span class="flow-step">5. 保存分析流程</span>
          </div>
        </div>
        <div class="flow-overview-side" v-if="!currentFlow.process_id">
          <div class="flow-side-title">可视化节点类型</div>
          <div class="flow-node-legend">
            <span v-for="nt in flowNodeTypes" :key="nt.type" class="flow-legend-chip" :style="{ borderColor: nt.borderColor, color: nt.stroke }">
              {{ nt.icon }} {{ nt.label }}
            </span>
          </div>
        </div>
        <div class="flow-overview-side" v-else>
          <div class="flow-side-title">当前流程图</div>
          <div class="flow-side-current">{{ currentFlow.process_name }}</div>
          <div class="flow-side-stats">
            <span>节点 {{ flowNodes.length }}</span>
            <span>连线 {{ flowEdges.length }}</span>
            <span>版本 {{ currentFlow.version || '1.0' }}</span>
          </div>
          <p class="flow-side-desc">{{ currentFlow.process_desc || '当前流程图尚未填写说明，可在流程基础信息中补充。' }}</p>
        </div>
      </div>
      <div class="toolbar">
        <el-button type="success" size="small" @click="openProcessGuide">AI 生成流程图</el-button>
        <el-button type="primary" size="small" @click="showCreateProcess">新建分析流程图</el-button>
        <el-button size="small" @click="loadProcesses">刷新流程列表</el-button>
        <span v-if="currentFlow.process_id" class="flow-breadcrumb">
          × <el-tag>{{ currentFlow.process_name }}</el-tag>
          <el-button size="small" @click="closeFlowEditor">返回列表</el-button>
        </span>
      </div>

      <!-- Flow List View -->
      <div v-if="!currentFlow.process_id" class="flow-list-panel">
        <div class="flow-cards">
          <el-card v-for="proc in processes" :key="proc.process_id" class="flow-card" shadow="hover" @click="openFlowEditor(proc)">
            <div class="flow-card-header">
              <h4>{{ proc.process_name }}</h4>
              <el-tag :type="proc.status === 'PUBLISHED' ? 'success' : 'info'" size="small">{{ proc.status === 'PUBLISHED' ? '已发布' : '草稿' }}</el-tag>
            </div>
            <p class="flow-desc">{{ proc.process_desc || '暂无描述' }}</p>
            <div class="flow-meta">
              <span>v{{ proc.version }}</span>
              <span>创建人: {{ proc.created_by }}</span>
              <span>{{ formatDate(proc.created_at) }}</span>
            </div>
            <div class="flow-actions">
              <el-button size="small" type="primary" link @click.stop="openFlowEditor(proc)">进入流程图</el-button>
              <el-button size="small" type="danger" link @click.stop="deleteProcessItem(proc.process_id)">删除</el-button>
            </div>
          </el-card>
          <el-empty v-if="processes.length === 0" description="暂无分析流程图，点击上方“新建分析流程图”开始可视化创建">
            <el-button type="primary" @click="showCreateProcess">新建分析流程图</el-button>
          </el-empty>
        </div>
      </div>

      <!-- Flow Editor Canvas -->
      <div v-else class="flow-editor">
        <div class="flow-editor-summary">
          <div>
            <h4>{{ currentFlow.process_name }}</h4>
            <p>{{ currentFlow.process_desc || '当前流程图用于描述分析如何逐步开展，可通过拖拽、连线和节点配置来表达分析步骤。' }}</p>
            <div class="flow-editor-hint">
              优先使用两种连线方式：
              1. 从节点右侧连接点直接拖到目标节点。
              2. 先后点击两个节点，再点“连接已选节点”。
            </div>
          </div>
          <div class="flow-editor-summary-stats">
            <span>节点 {{ flowNodes.length }}</span>
            <span>连线 {{ flowEdges.length }}</span>
          </div>
        </div>
        <div class="flow-editor-toolbar">
          <div class="node-palette">
            <span class="palette-label">拖拽节点到画布:</span>
            <div
              v-for="nt in flowNodeTypes"
              :key="nt.type"
              class="palette-node"
              :style="{ backgroundColor: nt.color, borderColor: nt.borderColor }"
              draggable="true"
              @dragstart="onFlowNodeDragStart($event, nt)"
            >
              {{ nt.icon }} {{ nt.label }}
            </div>
          </div>
          <div class="flow-editor-actions">
            <span class="flow-selection-state">已选 {{ flowSelectedNodeIds.length }}/2</span>
            <el-button size="small" type="primary" plain :disabled="flowSelectedNodeIds.length !== 2" @click="connectSelectedFlowNodes">
              连接已选节点
            </el-button>
            <el-button size="small" plain :disabled="flowSelectedNodeIds.length === 0" @click="clearFlowSelection">清空选中</el-button>
            <el-button size="small" @click="saveFlowToServer" :loading="savingFlow">💾 保存流程图</el-button>
            <el-button size="small" type="warning" @click="resetFlowFromServer" :disabled="!currentFlow.process_json">↩ 重置画布</el-button>
            <el-button size="small" type="danger" @click="deleteFlowProcess">🗑 删除流程图</el-button>
          </div>
        </div>
        <div class="flow-editor-body">
          <div
            class="flow-canvas"
            ref="flowCanvasRef"
            @dragover.prevent="onFlowCanvasDragOver"
            @drop.prevent="onFlowCanvasDrop"
            @mousemove="onFlowMouseMove"
            @mouseup="onFlowMouseUp"
            @mouseleave="onFlowMouseUp"
          >
            <svg width="100%" height="100%" :viewBox="`0 0 ${flowCanvasSize.w} ${flowCanvasSize.h}`">
              <defs>
                <marker id="flowArrowhead" markerWidth="10" markerHeight="7" refX="10" refY="3.5" orient="auto">
                  <polygon points="0 0, 10 3.5, 0 7" fill="#666" />
                </marker>
              </defs>
              <!-- Flow Edges -->
              <g v-for="(edge, ei) in flowEdges" :key="ei">
                <path
                  :d="getFlowEdgePath(edge)"
                  fill="none"
                  stroke="#666"
                  stroke-width="2.5"
                  marker-end="url(#flowArrowhead)"
                />
              </g>
              <!-- Flow Nodes -->
              <g
                v-for="(node, ni) in flowNodes"
                :key="node.id"
                :transform="`translate(${node.position.x}, ${node.position.y})`"
                class="flow-node-group"
                @mousedown.stop="onFlowNodeMouseDown($event, node)"
                @click.stop="selectFlowNode(node)"
              >
                <rect
                  :width="150" :height="60" :rx="getFlowNodeShape(node.type).rx" :ry="getFlowNodeShape(node.type).ry"
                  :fill="getFlowNodeShape(node.type).fill"
                  :stroke="isFlowNodeSelected(node.id) || flowDragging?.id === node.id || flowConfigNode?.id === node.id ? '#409EFF' : getFlowNodeShape(node.type).stroke"
                  :stroke-width="isFlowNodeSelected(node.id) || flowDragging?.id === node.id || flowConfigNode?.id === node.id ? 3 : 2"
                  :style="{ cursor: flowConnecting ? 'crosshair' : 'grab' }"
                />
                <text x="75" y="28" text-anchor="middle" :fill="getFlowNodeShape(node.type).textColor" font-size="13" font-weight="bold">{{ node.label }}</text>
                <text x="75" y="48" text-anchor="middle" :fill="getFlowNodeShape(node.type).textColor" font-size="10" opacity="0.8">{{ node.typeName }}</text>
                <!-- Delete button -->
                <circle cx="145" cy="6" r="10" fill="#f56c6c" style="cursor:pointer" @click.stop="deleteFlowNode(ni)" />
                <text x="145" y="10" text-anchor="middle" fill="#fff" font-size="12" style="cursor:pointer" @click.stop="deleteFlowNode(ni)">✕</text>
                <!-- Input / Output handles -->
                <circle cx="0" cy="30" r="9" fill="#dbeafe" stroke="#60a5fa" stroke-width="2" />
                <circle
                  cx="150"
                  cy="30"
                  r="11"
                  fill="#409EFF"
                  stroke="#ffffff"
                  stroke-width="2"
                  class="flow-handle"
                  @mousedown.stop="startFlowConnection($event, node)"
                />
                <text
                  x="150"
                  y="34"
                  text-anchor="middle"
                  fill="#fff"
                  font-size="10"
                  font-weight="bold"
                  class="flow-handle-label"
                  @mousedown.stop="startFlowConnection($event, node)"
                >→</text>
              </g>
              <!-- Connection indicator -->
              <path
                v-if="flowConnecting"
                :d="getFlowPreviewPath(flowConnecting)"
                fill="none"
                stroke="#409EFF"
                stroke-width="2.5"
                stroke-dasharray="6,5"
              />
            </svg>
          </div>
          <div class="flow-side-panel">
            <div class="flow-config-panel" v-if="flowConfigNode">
              <h4>分析步骤配置: {{ flowConfigNode.label }}</h4>
              <el-form label-width="80px" size="small">
                <el-form-item label="节点标签">
                  <el-input v-model="flowConfigNode.label" />
                </el-form-item>
                <el-form-item label="节点类型">
                  <el-tag>{{ flowConfigNode.typeName }}</el-tag>
                </el-form-item>
                <el-form-item label="描述">
                  <el-input v-model="flowConfigNode.desc" type="textarea" :rows="2" placeholder="节点描述" />
                </el-form-item>
                <!-- DataInput specific -->
                <template v-if="flowConfigNode.type === 'dataInput'">
                  <el-form-item label="本体实体">
                    <el-input v-model="flowConfigNode.config.ontologyEntity" placeholder="如: DefectRecord" />
                  </el-form-item>
                  <el-form-item label="使用方式">
                    <el-select v-model="flowConfigNode.config.usageMode">
                      <el-option label="查询模式" value="query" />
                      <el-option label="聚合分析" value="aggregate" />
                      <el-option label="对比分析" value="compare" />
                      <el-option label="追溯分析" value="trace" />
                    </el-select>
                  </el-form-item>
                </template>
                <!-- Analysis specific -->
                <template v-if="flowConfigNode.type === 'analysis'">
                  <el-form-item label="分析类型">
                    <el-input v-model="flowConfigNode.config.analysisType" placeholder="如: classification" />
                  </el-form-item>
                </template>
                <!-- Action specific -->
                <template v-if="flowConfigNode.type === 'action'">
                  <el-form-item label="操作类型">
                    <el-select v-model="flowConfigNode.config.actionType">
                      <el-option label="生成报告" value="generateReport" />
                      <el-option label="发送通知" value="sendNotification" />
                      <el-option label="触发告警" value="triggerAlert" />
                    </el-select>
                  </el-form-item>
                </template>
              </el-form>
            </div>
            <div v-else class="flow-config-empty">
              <h4>分析步骤配置</h4>
              <p>点击右侧画布中的任意节点，即可在这里查看和编辑该步骤的参数配置。</p>
              <p>从节点右侧蓝色连接点拖到目标节点，可以快速创建流程边。</p>
              <p>也可以先后选中两个节点，再点击上方“连接已选节点”。选中顺序即连线方向。</p>
            </div>
          </div>
        </div>
      </div>
    </div>

    <el-dialog v-model="entityDialogVisible" :title="entityDialogMode === 'edit' ? '编辑本体实体' : '添加本体实体'" width="500px">
      <el-form :model="entityForm" label-width="100px">
        <el-form-item label="实体名称"><el-input v-model="entityForm.entity_name" placeholder="英文名称，如DefectRecord" /></el-form-item>
        <el-form-item label="显示名称"><el-input v-model="entityForm.entity_display_name" placeholder="中文名称，如缺陷记录" /></el-form-item>
        <el-form-item label="描述"><el-input v-model="entityForm.entity_desc" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="构建方式">
          <el-radio-group v-model="entityForm.build_type">
            <el-radio value="TABLE">Management Table</el-radio>
            <el-radio value="VIEW">Management View</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="图标颜色">
          <el-color-picker v-model="entityForm.color" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="entityDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveEntity" :loading="loading">{{ entityDialogMode === 'edit' ? '保存' : '添加' }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="relationDialogVisible" :title="relationDialogMode === 'edit' ? '编辑本体关系' : '添加本体关系'" width="500px">
      <el-form :model="relationForm" label-width="100px">
        <el-form-item label="源实体">
          <el-select v-model="relationForm.source_entity_id" placeholder="选择源实体">
            <el-option v-for="e in graphNodes" :key="e.id" :label="e.displayName || e.name" :value="e.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="目标实体">
          <el-select v-model="relationForm.target_entity_id" placeholder="选择目标实体">
            <el-option v-for="e in graphNodes" :key="e.id" :label="e.displayName || e.name" :value="e.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="关系名称"><el-input v-model="relationForm.relation_name" maxlength="12" show-word-limit placeholder="如：导致、属于、包含（不重复两端实体名）" /></el-form-item>
        <el-form-item label="关系类型">
          <el-select v-model="relationForm.relation_type">
            <el-option label="一对一" value="ONE_TO_ONE" />
            <el-option label="一对多" value="ONE_TO_MANY" />
            <el-option label="多对多" value="MANY_TO_MANY" />
            <el-option label="继承" value="INHERITANCE" />
            <el-option label="关联" value="ASSOCIATION" />
          </el-select>
        </el-form-item>
        <el-form-item label="关系描述"><el-input v-model="relationForm.relation_desc" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="英文边名称">
          <el-input v-model="relationForm.relation_table_name" placeholder="如 BELONGS_TO，生成 ONTO_EDGE_BELONGS_TO" />
          <div class="form-help">仅支持英文、数字和下划线；留空时使用系统默认名称。</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button
          v-if="relationDialogMode === 'edit'"
          type="danger"
          plain
          @click="deleteRelation"
        >
          删除关系
        </el-button>
        <el-button @click="relationDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveRelation" :loading="loading">{{ relationDialogMode === 'edit' ? '保存' : '添加' }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="propertyDialogVisible" :title="propertyDialogMode === 'edit' ? '修改本体属性' : '添加本体属性'" width="500px">
      <el-form :model="propertyForm" label-width="100px">
        <el-form-item label="属性名称"><el-input v-model="propertyForm.property_name" placeholder="英文名如defect_id" /></el-form-item>
        <el-form-item label="显示名称"><el-input v-model="propertyForm.property_display_name" placeholder="中文名如缺陷ID" /></el-form-item>
        <el-form-item label="数据类型">
          <el-select v-model="propertyForm.data_type">
            <el-option label="VARCHAR2" value="VARCHAR2" />
            <el-option label="NUMBER" value="NUMBER" />
            <el-option label="NUMBER(10)" value="NUMBER(10)" />
            <el-option label="DATE" value="DATE" />
            <el-option label="TIMESTAMP" value="TIMESTAMP" />
            <el-option label="CLOB" value="CLOB" />
          </el-select>
        </el-form-item>
        <el-form-item label="是否主键"><el-switch v-model="propertyForm.is_primary_key" active-value="Y" inactive-value="N" /></el-form-item>
        <el-form-item label="是否可空"><el-switch v-model="propertyForm.is_nullable" active-value="Y" inactive-value="N" /></el-form-item>
        <el-form-item label="描述"><el-input v-model="propertyForm.property_desc" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="propertyDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveProperty" :loading="loading">{{ propertyDialogMode === 'edit' ? '保存' : '添加' }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="guideDialogVisible" title="Guide 自动生成业务实体与关系" width="1180px" top="4vh">
      <div class="guide-dialog">
        <section class="guide-banner">
          <div class="guide-banner-main">
            <div class="guide-banner-title">
              <span>当前业务分析域</span>
              <el-tag v-if="currentDomainName" type="success" effect="light">{{ currentDomainName }}</el-tag>
              <span v-else class="guide-banner-empty">请先选择业务分析域</span>
            </div>
            <p class="guide-banner-desc">
              {{ currentDomainDesc || '当前业务分析域尚未填写说明。建议在下方业务说明文档中补充业务边界、核心对象和关键关系。' }}
            </p>
          </div>
          <div class="guide-banner-side">
            <span>覆盖同名对象说明</span>
            <el-switch v-model="guideForm.overwrite_existing" />
          </div>
        </section>
        <section class="guide-steps-shell">
          <div class="guide-steps-nav">
            <button
              v-for="step in guideStepOptions"
              :key="step.value"
              type="button"
              class="guide-step-chip"
              :class="{
                'is-active': guideStep === step.value,
                'is-unlocked': canEnterGuideStep(step.value)
              }"
              :disabled="!canEnterGuideStep(step.value)"
              @click="setGuideStep(step.value)"
            >
              <span class="guide-step-index">{{ step.value }}</span>
              <span class="guide-step-label">{{ step.label }}</span>
            </button>
          </div>
          <div class="guide-step-caption">
            {{ activeGuideStepDescription }}
          </div>
        </section>

        <section v-if="guideStep === 1" class="guide-step-body">
          <section class="guide-toolbar">
            <el-segmented
              v-model="guideForm.table_source_mode"
              :options="[
                { label: '数据库表', value: 'database' },
                { label: 'DDL文件', value: 'ddl' }
              ]"
              class="guide-toolbar-item"
              @change="handleGuideTableSourceModeChange"
            />
            <el-select
              v-model="guideForm.source_id"
              placeholder="选择数据库连接"
              filterable
              class="guide-toolbar-item"
              :loading="guideSourceLoading"
              :disabled="guideForm.table_source_mode === 'ddl'"
              @change="handleGuideSourceChange"
            >
              <el-option
                v-for="source in guideDataSources"
                :key="source.source_id"
                :label="source.source_name"
                :value="source.source_id"
              />
            </el-select>

            <el-select
              v-model="guideForm.schema"
              placeholder="选择 Schema"
              filterable
              class="guide-toolbar-item"
              :disabled="guideForm.table_source_mode === 'ddl' || !guideForm.source_id"
              :loading="guideSchemaLoading"
              @change="handleGuideSchemaChange"
            >
              <el-option v-for="schema in guideSchemaOptions" :key="schema" :label="schema" :value="schema" />
            </el-select>

            <el-select
              v-model="guideForm.model_config_id"
              placeholder="选择大模型"
              filterable
              clearable
              class="guide-toolbar-item"
            >
              <el-option
                v-for="model in guideModelOptions"
                :key="model.config_id"
                :label="formatGuideModelOption(model)"
                :value="model.config_id"
              />
            </el-select>

            <el-button :disabled="guideForm.table_source_mode === 'ddl' || !guideForm.source_id" @click="loadGuideTables">刷新关系表</el-button>
          </section>

          <section class="guide-toolbar">
            <el-select
              v-model="guideForm.generation_strategy"
              class="guide-toolbar-item"
              @change="handleGuideGenerationStrategyChange"
            >
              <el-option label="结构化领域生成" value="structured_domain_pipeline" />
              <el-option label="LLM模型辅助" value="llm_first" />
            </el-select>
            <el-select
              v-if="guideForm.generation_strategy === 'llm_first'"
              v-model="guideForm.semantic_type_code"
              clearable
              :placeholder="guideSemanticTypeOptions.length ? '选择本次业务语义' : '正在加载业务语义配置'"
              class="guide-toolbar-item"
              @change="handleGuideSemanticTypeChange"
            >
              <el-option
                v-for="semanticType in guideSemanticTypeOptions"
                :key="semanticType.type_code"
                :label="semanticType.type_name"
                :value="semanticType.type_code"
              >
                <span>{{ semanticType.type_name }}</span>
                <span v-if="semanticType.semantic_desc" class="guide-mode-option-desc">{{ semanticType.semantic_desc }}</span>
              </el-option>
            </el-select>
            <el-select
              v-else
              v-model="guideForm.business_scenario"
              clearable
              placeholder="业务目标"
              class="guide-toolbar-item"
            >
              <el-option label="SFR根因分析" value="SFR_ROOTCAUSE" />
              <el-option label="缺陷分析" value="DEFECT_ANALYSIS" />
            </el-select>
            <div class="guide-panel-hint guide-toolbar-item" style="grid-column: span 2;">
              {{ guideForm.generation_strategy === 'llm_first'
                ? `LLM 模型辅助将按所选业务语义「${currentBusinessTypeName || '未配置'}」生成本体对象和关系。`
                : '结构化领域生成会优先执行问卷/DDL/规则数据的结构化分析，再生成 canonical 本体与标准化视图计划。' }}
            </div>
          </section>

          <section v-if="guideForm.table_source_mode === 'database'" class="guide-toolbar">
            <el-select
              v-model="guideForm.rule_table_name"
              placeholder="可选：单独指定规则表"
              filterable
              clearable
              class="guide-toolbar-item"
              :disabled="!guideForm.source_id"
            >
              <el-option
                v-for="table in guideTables"
                :key="`rule-${table.owner}.${table.table_name}`"
                :label="`${table.table_name}${table.comments ? ` (${table.comments})` : ''}`"
                :value="table.table_name"
              />
            </el-select>
            <div class="guide-panel-hint" style="grid-column: span 3;">
              数据库表模式下，若存在类似 `SPEC_LIMIT` 的规则表，可在这里单独指定；它会作为缺陷识别依据参与 Guide 生成，但不占用业务关系表选择。
            </div>
          </section>

          <div class="guide-layout">
            <section class="guide-document-panel">
              <div class="guide-panel-head">
                <span class="guide-panel-title">业务说明文档</span>
                <div class="guide-document-actions">
                  <el-upload
                    :show-file-list="false"
                    :auto-upload="false"
                    accept=".txt,.md,.docx,.pdf"
                    @change="handleGuideDocumentFileChange"
                  >
                    <el-button :icon="UploadFilled" :loading="guideUploadLoading">上传文档</el-button>
                  </el-upload>
                </div>
              </div>
              <div v-if="guideUploadedDocument" class="guide-upload-meta">
                已解析文件：{{ guideUploadedDocument.file_name }}
                <span>类型 {{ guideUploadedDocument.file_type || '-' }}</span>
                <span>{{ guideUploadedDocument.char_count }} 字</span>
              </div>
              <el-input
                v-model="guideForm.business_document"
                type="textarea"
                :rows="17"
                placeholder="粘贴业务说明、流程说明、对象定义、关系规则等文档内容。结构化 Guide 会优先抽取业务边界、关键站位、规则范围和追溯链路。"
              />
              <div class="guide-panel-hint">
                支持上传 `txt / md / docx / pdf` 文档，解析结果会自动追加到当前说明文档输入框。建议文档中包含：业务对象定义、对象间约束、关键关系、口径说明、典型分析问题。
              </div>
            </section>

            <section class="guide-table-panel">
              <div class="guide-panel-head">
                <span class="guide-panel-title">业务关系表</span>
                <div class="guide-panel-actions">
                  <span class="guide-panel-meta">已选 {{ guideForm.relation_tables.length }}</span>
                  <el-upload
                    v-if="guideForm.table_source_mode === 'ddl'"
                    :show-file-list="false"
                    :auto-upload="false"
                    multiple
                    accept=".sql,.ddl,.txt,.md"
                    @change="handleGuideDDLFileChange"
                  >
                    <el-button size="small" text :icon="UploadFilled" :loading="guideDDLUploadLoading">上传DDL文件</el-button>
                  </el-upload>
                  <el-upload
                    :show-file-list="false"
                    :auto-upload="false"
                    multiple
                    accept=".sql,.ddl,.txt,.md"
                    @change="handleGuideRuleFileChange"
                  >
                    <el-button size="small" text :icon="UploadFilled" :loading="guideRuleUploadLoading">上传规则数据</el-button>
                  </el-upload>
                  <el-button
                    size="small"
                    text
                    :disabled="guideTableLoading || activeGuideTables.length === 0"
                    @click="selectAllGuideTables"
                  >
                    全选
                  </el-button>
                  <el-button
                    size="small"
                    text
                    :disabled="guideTableLoading || guideForm.relation_tables.length === 0"
                    @click="clearGuideTableSelection"
                  >
                    清空
                  </el-button>
                </div>
              </div>
              <el-input
                v-model="guideTableKeyword"
                placeholder="按表名或表描述筛选"
                clearable
                class="guide-table-search"
              />
              <div v-if="guideForm.table_source_mode === 'ddl' && guideUploadedDDLFiles.length" class="guide-upload-meta">
                已解析DDL文件：{{ guideUploadedDDLFiles.map(item => item.file_name).join('，') }}
                <span>文件 {{ guideUploadedDDLFiles.length }} 个</span>
                <span>表 {{ guideDDLSchemaTables.length }} 个</span>
              </div>
              <div v-if="guideUploadedRuleFiles.length" class="guide-upload-meta">
                已解析规则文件：{{ guideUploadedRuleFiles.map(item => item.file_name).join('，') }}
                <span>文件 {{ guideUploadedRuleFiles.length }} 个</span>
                <span>规则集 {{ guideRuleDatasets.length }} 组</span>
              </div>
              <div class="guide-table-list" v-loading="guideTableLoading">
                <el-checkbox-group v-model="guideForm.relation_tables" class="guide-checkbox-group">
                  <label
                    v-for="table in filteredGuideTables"
                    :key="`${table.owner}.${table.table_name}`"
                    class="guide-table-item"
                  >
                    <el-checkbox :label="table.table_name">
                      <span class="guide-table-name">{{ table.table_name }}</span>
                    </el-checkbox>
                    <span class="guide-table-owner">{{ table.owner }}</span>
                    <span class="guide-table-comment">{{ table.comments || '当前无表描述' }}</span>
                  </label>
                </el-checkbox-group>
                <el-empty
                  v-if="!guideTableLoading && filteredGuideTables.length === 0"
                  :description="guideForm.table_source_mode === 'ddl' ? (guideUploadedDDLFiles.length ? '当前DDL文件中没有匹配表' : '请先上传数据库DDL文件') : (guideForm.source_id ? '当前条件下没有可选关系表' : '请先选择数据库连接')"
                />
              </div>
              <div v-if="guideForm.relation_tables.length && guideForm.generation_strategy !== 'llm_first'" class="guide-pattern-panel">
                <div class="guide-panel-head">
                  <span class="guide-panel-title">业务语义模式（手工选择）</span>
                  <span class="guide-panel-meta">本次已选择 {{ guideForm.enabled_patterns.length }}</span>
                </div>
                <div class="guide-pattern-type-hint">当前业务类型：{{ currentBusinessTypeName || '未配置' }}。{{ currentBusinessTypeDesc || '语义模式由“业务类型语义管理”维护。' }}</div>
                <el-checkbox-group v-model="guideForm.enabled_patterns">
                  <el-checkbox
                    v-for="pattern in guidePatternOptions"
                    :key="pattern.value"
                    :label="pattern.value"
                  >
                    {{ pattern.label }}<span v-if="pattern.description" class="guide-pattern-desc">：{{ pattern.description }}</span>
                  </el-checkbox>
                </el-checkbox-group>
                <div v-if="guideForm.enabled_patterns.length" class="guide-selected-patterns">
                  <span>本次生成已选择：</span>
                  <el-tag
                    v-for="patternCode in guideForm.enabled_patterns"
                    :key="patternCode"
                    closable
                    size="small"
                    @close="removeGuidePattern(patternCode)"
                  >{{ guidePatternLabel(patternCode) }}</el-tag>
                </div>
                <div v-else class="guide-pattern-empty">请按本次业务目标手工勾选需要带入 LLM 提示词的语义模式。</div>
              </div>
            </section>
          </div>
        </section>

        <section v-else-if="guideStep === 2" class="guide-step-body">
          <el-empty v-if="!guidePreview" description="请先在第 1 步生成本体对象与关系建议。" :image-size="76" />
          <div v-else-if="isLlmFirstGuide" class="guide-preview">
            <div class="guide-preview-grid">
              <section class="guide-preview-panel">
                <div class="guide-panel-title">业务语义范围确认</div>
                <div class="guide-preview-summary">生成策略：LLM模型辅助</div>
                <div class="guide-preview-summary">业务类型：{{ currentBusinessTypeName || '-' }}</div>
                <div class="guide-preview-summary">本次语义模式：{{ guideForm.enabled_patterns.map(guidePatternLabel).join(' / ') || '未选择' }}</div>
                <div class="guide-preview-summary">MVP 范围：{{ guidePreview.ontology_design_document?.mvp_scope || '模型未返回范围说明' }}</div>
                <div class="guide-preview-summary">范围说明：{{ guidePreview.ontology_design_document?.scope_reasoning || '-' }}</div>
              </section>

              <section class="guide-preview-panel">
                <div class="guide-panel-title">已读取源表</div>
                <div class="guide-preview-summary">已选择 {{ (guidePreview.selected_tables || []).length }} 张表</div>
                <div class="guide-table-chip-list">
                  <el-tag v-for="tableName in guidePreview.selected_tables || []" :key="tableName" size="small" effect="plain">{{ tableName }}</el-tag>
                </div>
              </section>

              <section class="guide-preview-panel">
                <div class="guide-panel-title">对象范围确认</div>
                <div class="guide-preview-summary">建议实体：{{ guidePreview.entities?.length || 0 }} 个</div>
                <div class="guide-preview-summary">建议关系：{{ guidePreview.relations?.length || 0 }} 条</div>
                <div class="guide-preview-summary">首期对象：{{ (guidePreview.ontology_design_document?.included_entities || []).map((item: any) => item.entityDisplayName || item.entityName).join(' / ') || '未限定，将在本体预览中查看' }}</div>
              </section>

              <section class="guide-preview-panel">
                <div class="guide-panel-title">延后项与实施说明</div>
                <div v-for="item in guidePreview.ontology_design_document?.excluded_or_deferred || []" :key="`${item.name}-${item.reason}`" class="guide-preview-summary">{{ item.name }}：{{ item.reason || '延后处理' }}</div>
                <div v-if="!(guidePreview.ontology_design_document?.excluded_or_deferred || []).length" class="guide-preview-summary">暂无延后项。</div>
                <div v-for="note in guidePreview.ontology_design_document?.implementation_notes || []" :key="note" class="guide-preview-summary">{{ note }}</div>
              </section>
            </div>
          </div>
          <div v-else class="guide-preview">
            <div class="guide-preview-grid">
              <section class="guide-preview-panel">
                <div class="guide-panel-title">场景事实</div>
                <div class="guide-preview-summary">业务场景：{{ guidePreview.document_facts?.scenario_name || guidePreview.business_scenario || '-' }}</div>
                <div class="guide-preview-summary">产品代号：{{ (guidePreview.document_facts?.product_codes || []).join(' / ') || '-' }}</div>
                <div class="guide-preview-summary">历史知识来源：{{ (guidePreview.document_facts?.history_knowledge_sources || []).join(' / ') || '-' }}</div>
                <div class="guide-preview-summary">分析目标：{{ (guidePreview.document_facts?.analysis_goals || []).join(' / ') || '-' }}</div>
                <div class="guide-preview-summary">追溯链路：{{ (guidePreview.document_facts?.trace_chain || []).join(' -> ') || '-' }}</div>
              </section>

              <section class="guide-preview-panel">
                <div class="guide-panel-title">规则范围确认</div>
                <div class="guide-preview-summary">规则表：{{ guidePreview.rule_analysis?.rule_table_name || guidePreview.rule_summary?.rule_table_name || '-' }}</div>
                <div class="guide-preview-summary">阈值规则数：{{ guidePreview.rule_analysis?.threshold_rule_count || 0 }}</div>
                <div class="guide-preview-summary">判定口径：{{ guidePreview.rule_analysis?.oos_logic || '-' }}</div>
                <div class="guide-control-group">
                  <div class="guide-control-label">首期指标族</div>
                  <el-checkbox-group v-model="guideForm.focus_metric_families" class="guide-tag-checkboxes">
                    <el-checkbox
                      v-for="family in (guidePreview.rule_analysis?.family_stats || [])"
                      :key="family.family_name"
                      :label="family.family_name"
                    >
                      {{ family.family_name }} ({{ family.threshold_metric_count || 0 }}/{{ family.metric_count || 0 }})
                    </el-checkbox>
                  </el-checkbox-group>
                </div>
              </section>

              <section class="guide-preview-panel">
                <div class="guide-panel-title">关键表识别</div>
                <div class="guide-preview-summary">产品主表：{{ guidePreview.schema_analysis?.key_tables?.product_index_table || '-' }}</div>
                <div class="guide-preview-summary">过程表：{{ guidePreview.schema_analysis?.key_tables?.process_table || '-' }}</div>
                <div class="guide-preview-summary">测试表：{{ (guidePreview.schema_analysis?.key_tables?.test_tables || []).join(' / ') || '-' }}</div>
                <div class="guide-preview-summary">AA 表：{{ (guidePreview.schema_analysis?.key_tables?.aa_feature_tables || []).join(' / ') || '-' }}</div>
                <div class="guide-preview-summary">Alarm 表：{{ (guidePreview.schema_analysis?.key_tables?.alarm_tables || []).join(' / ') || '-' }}</div>
              </section>

              <section class="guide-preview-panel">
                <div class="guide-panel-title">重点站位确认</div>
                <div class="guide-control-group">
                  <div class="guide-control-label">首期站位</div>
                  <el-checkbox-group v-model="guideForm.focus_stations" class="guide-tag-checkboxes">
                    <el-checkbox
                      v-for="station in (guidePreview.schema_analysis?.focus_stations || [])"
                      :key="station.station_code"
                      :label="station.station_code"
                    >
                      {{ station.station_code }} ({{ station.evidence_column_count || 0 }})
                    </el-checkbox>
                  </el-checkbox-group>
                </div>
                <div class="guide-control-group">
                  <div class="guide-control-label">历史案例来源</div>
                  <el-checkbox-group v-model="guideForm.history_case_sources" class="guide-tag-checkboxes">
                    <el-checkbox
                      v-for="source in availableGuideHistorySources"
                      :key="source"
                      :label="source"
                    >
                      {{ source }}
                    </el-checkbox>
                  </el-checkbox-group>
                </div>
              </section>
            </div>
          </div>
        </section>

        <section v-else-if="guideStep === 3" class="guide-step-body">
          <el-empty v-if="!guidePreview" description="请先生成预览结果。" :image-size="76" />
          <section v-else class="guide-preview">
            <div class="guide-preview-head">
              <div class="guide-preview-tags">
                <el-tag size="small" type="success">Canonical Model</el-tag>
                <span>实体 {{ guidePreview.entities?.length || 0 }}</span>
                <span>关系 {{ guidePreview.relations?.length || 0 }}</span>
                <span>对象分组 {{ guidePreview.canonical_model?.entity_groups?.length || 0 }}</span>
              </div>
              <div v-if="guidePreview.apply_result" class="guide-apply-summary">
                <el-tag size="small" type="success">已应用</el-tag>
                <span>新增实体 {{ guidePreview.apply_result.entities?.created || 0 }}</span>
                <span>新增关系 {{ guidePreview.apply_result.relations?.created || 0 }}</span>
              </div>
            </div>
            <div class="guide-preview-grid">
              <section class="guide-preview-panel">
                <div class="guide-panel-title">范围说明</div>
                <div class="guide-preview-summary">MVP 范围：{{ guidePreview.ontology_design_document?.mvp_scope || '-' }}</div>
                <div class="guide-preview-summary">范围说明：{{ guidePreview.ontology_design_document?.scope_reasoning || '-' }}</div>
                <div class="guide-preview-summary">首期对象：{{ (guidePreview.ontology_design_document?.included_entities || []).map((item: any) => item.entityDisplayName || item.entityName).join(' / ') || '-' }}</div>
                <div class="guide-preview-summary">首期关系：{{ (guidePreview.ontology_design_document?.included_relations || []).map((item: any) => item.relationName).join(' / ') || '-' }}</div>
              </section>

              <section class="guide-preview-panel">
                <div class="guide-panel-title">缺陷语义分类</div>
                <el-table :data="guidePreview.metric_semantics?.semantic_categories || []" border stripe size="small" max-height="240">
                  <el-table-column prop="semantic_label" label="语义缺陷" min-width="150" />
                  <el-table-column label="规格族" min-width="150" show-overflow-tooltip>
                    <template #default="{ row }">{{ (row.matched_families || []).join(' / ') || '-' }}</template>
                  </el-table-column>
                  <el-table-column label="示例指标" min-width="220" show-overflow-tooltip>
                    <template #default="{ row }">{{ (row.metric_examples || []).join(', ') || '-' }}</template>
                  </el-table-column>
                </el-table>
              </section>

              <section class="guide-preview-panel">
                <div class="guide-panel-title">实体建议</div>
                <el-table :data="guidePreview.entities || []" border stripe size="small" max-height="260">
                  <el-table-column prop="entityDisplayName" label="显示名" min-width="140" />
                  <el-table-column prop="entityName" label="实体名" min-width="160" />
                  <el-table-column prop="buildType" label="构建方式" width="100" />
                  <el-table-column label="来源表" min-width="180" show-overflow-tooltip>
                    <template #default="{ row }">{{ (row.sourceHints || []).join(', ') || '-' }}</template>
                  </el-table-column>
                </el-table>
              </section>

              <section class="guide-preview-panel">
                <div class="guide-panel-title">关系建议</div>
                <el-table :data="guidePreview.relations || []" border stripe size="small" max-height="260">
                  <el-table-column prop="sourceEntityName" label="源实体" min-width="130" />
                  <el-table-column prop="relationName" label="关系名称" min-width="120" />
                  <el-table-column prop="targetEntityName" label="目标实体" min-width="130" />
                  <el-table-column prop="relationType" label="关系类型" width="120" />
                  <el-table-column label="证据表" min-width="180" show-overflow-tooltip>
                    <template #default="{ row }">{{ (row.evidenceTables || []).join(', ') || '-' }}</template>
                  </el-table-column>
                </el-table>
              </section>
            </div>
          </section>
        </section>

        <section v-else class="guide-step-body">
          <el-empty v-if="!guidePreview" description="请先生成预览结果。" :image-size="76" />
          <section v-else class="guide-preview">
            <div class="guide-preview-grid">
              <section class="guide-preview-panel">
                <div class="guide-panel-title">标准化视图计划</div>
                <el-table :data="guidePreview.view_plan?.standardized_views || []" border stripe size="small" max-height="280">
                  <el-table-column prop="view_name" label="视图名" min-width="180" />
                  <el-table-column label="来源表" min-width="220" show-overflow-tooltip>
                    <template #default="{ row }">{{ (row.source_tables || []).join(', ') || '-' }}</template>
                  </el-table-column>
                  <el-table-column prop="purpose" label="用途" min-width="220" show-overflow-tooltip />
                  <el-table-column label="部署" width="90">
                    <template #default="{ row }">
                      <el-tag size="small" :type="row.deploy ? 'success' : 'info'">{{ row.deploy ? '是' : '否' }}</el-tag>
                    </template>
                  </el-table-column>
                </el-table>
              </section>

              <section class="guide-preview-panel">
                <div class="guide-panel-title">边视图骨架</div>
                <el-table :data="guidePreview.view_plan?.edge_views || []" border stripe size="small" max-height="280">
                  <el-table-column prop="view_name" label="边视图" min-width="180" />
                  <el-table-column label="依赖视图" min-width="220" show-overflow-tooltip>
                    <template #default="{ row }">{{ (row.source_views || row.source_tables || []).join(', ') || '-' }}</template>
                  </el-table-column>
                  <el-table-column prop="purpose" label="用途" min-width="220" show-overflow-tooltip />
                </el-table>
              </section>

              <section class="guide-preview-panel">
                <div class="guide-panel-title">Property Graph</div>
                <div class="guide-preview-summary">图名称：{{ guidePreview.deployment_design?.property_graph?.graph_name || guidePreview.view_plan?.graph_layer?.graph_name || '-' }}</div>
                <div class="guide-preview-summary">顶点对象：{{ (guidePreview.deployment_design?.property_graph?.vertex_entities || guidePreview.view_plan?.graph_layer?.vertex_entities || []).join(' / ') || '-' }}</div>
                <div class="guide-preview-summary">边关系：{{ (guidePreview.deployment_design?.property_graph?.edge_relations || guidePreview.view_plan?.graph_layer?.edge_relations || []).join(' / ') || '-' }}</div>
                <div class="guide-preview-summary">说明：{{ guidePreview.deployment_design?.property_graph?.note || guidePreview.view_plan?.graph_layer?.note || '-' }}</div>
              </section>

              <section class="guide-preview-panel">
                <div class="guide-panel-title">应用准备</div>
                <div class="guide-preview-summary">生成策略：{{ guidePreview.generation_strategy || '-' }}</div>
                <div class="guide-preview-summary">业务场景：{{ guidePreview.business_scenario || '-' }}</div>
                <div class="guide-preview-summary">首期指标族：{{ (guidePreview.focus_scope?.focus_metric_families || []).join(' / ') || '-' }}</div>
                <div class="guide-preview-summary">首期站位：{{ (guidePreview.focus_scope?.focus_stations || []).join(' / ') || '-' }}</div>
                <div class="guide-preview-summary">历史案例来源：{{ (guidePreview.focus_scope?.history_case_sources || []).join(' / ') || '-' }}</div>
              </section>
            </div>
          </section>
        </section>

        <el-alert
          v-if="guideRunMessage"
          :title="guideRunMessage"
          :type="guideRunState === 'error' ? 'error' : (guideRunState === 'warning' ? 'warning' : (guideRunState === 'success' ? 'success' : 'info'))"
          :closable="false"
          show-icon
          style="margin-top: 16px"
        />

      </div>
      <template #footer>
        <el-button @click="guideDialogVisible = false">关闭</el-button>
        <el-button :disabled="guideGenerating || guideApplying" @click="reloadGuidePreview">重新加载预览</el-button>
        <el-button v-if="guideStep > 1" @click="moveGuideStep(-1)">上一步</el-button>
        <el-button
          v-if="guideStep < 4 && canEnterGuideStep(guideStep + 1)"
          @click="moveGuideStep(1)"
        >
          下一步
        </el-button>
        <el-button type="primary" plain :loading="guideGenerating" @click="generateOntologyGuidePreview">生成本体对象</el-button>
        <el-button type="success" :loading="guideApplying" :disabled="!guidePreview?.entities?.length" @click="applyOntologyGuide">应用生成结果</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="processDialogVisible" title="新建分析流程图" width="500px">
      <el-form :model="processForm" label-width="100px">
        <el-form-item label="流程图名称"><el-input v-model="processForm.process_name" placeholder="如：缺陷归因分析流程" /></el-form-item>
        <el-form-item label="流程说明"><el-input v-model="processForm.process_desc" type="textarea" :rows="3" placeholder="说明该分析流程图用于描述哪些分析步骤和业务目标" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="processDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="createProcess" :loading="loading">创建并进入画布</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="naturalAdjustDialogVisible" title="自然语言调整本体对象与属性" width="1080px" top="6vh">
      <div class="natural-adjust-dialog">
        <section class="natural-adjust-banner">
          <div class="natural-adjust-banner-main">
            <div class="natural-adjust-banner-title">
              <span>当前业务分析域</span>
              <el-tag v-if="currentDomainName" type="success" effect="light">{{ currentDomainName }}</el-tag>
              <span v-else class="guide-banner-empty">请先选择业务分析域</span>
              <el-tag v-if="selectedNode" type="warning" effect="light">当前选中实体：{{ selectedNode.displayName || selectedNode.name }}</el-tag>
            </div>
            <p class="natural-adjust-banner-desc">
              描述你想如何调整当前本体对象、属性或关系。系统会先生成可检查的调整计划，再由你确认应用。
            </p>
          </div>
          <div class="natural-adjust-banner-side">
            <span>仅调整当前选中实体</span>
            <el-switch v-model="naturalAdjustForm.scope_selected_only" :disabled="!selectedNode" />
          </div>
        </section>

        <section class="natural-adjust-form">
          <el-select
            v-model="naturalAdjustForm.model_config_id"
            placeholder="选择大模型"
            filterable
            clearable
            class="natural-adjust-model"
          >
            <el-option
              v-for="model in guideModelOptions"
              :key="model.config_id"
              :label="formatGuideModelOption(model)"
              :value="model.config_id"
            />
          </el-select>
          <el-input
            v-model="naturalAdjustForm.instruction"
            type="textarea"
            :rows="7"
            placeholder="示例：为当前缺陷记录对象新增 defect_level 和 defect_category 两个属性，把 defect_desc 改成缺陷现象说明，并补充缺陷记录与工单的关联关系。"
          />
          <div class="natural-adjust-hint">
            建议明确写出需要新增、修改或删除的对象、属性、关系，系统会尽量按最小必要改动生成计划。
          </div>
        </section>

        <section v-if="naturalAdjustPreview" class="natural-adjust-preview">
          <div class="guide-preview-head">
            <div class="guide-preview-tags">
              <el-tag size="small" :type="naturalAdjustPreview.generation_mode === 'llm' ? 'success' : 'warning'">
                {{ naturalAdjustPreview.generation_mode === 'llm' ? 'LLM 生成' : '回退结果' }}
              </el-tag>
              <span v-if="naturalAdjustPreview.model">模型：{{ formatGuideModelOption(naturalAdjustPreview.model) }}</span>
              <span>对象动作 {{ naturalAdjustPreview.entityActions?.length || 0 }}</span>
              <span>属性动作 {{ naturalAdjustPreview.propertyActions?.length || 0 }}</span>
              <span>关系动作 {{ naturalAdjustPreview.relationActions?.length || 0 }}</span>
            </div>
          </div>
          <div class="natural-adjust-summary">{{ naturalAdjustPreview.summary || '当前未生成总结说明。' }}</div>
          <div class="natural-adjust-grid">
            <section class="guide-preview-panel">
              <div class="guide-panel-title">对象调整</div>
              <el-table :data="naturalAdjustPreview.entityActions || []" border stripe size="small" max-height="240">
                <el-table-column prop="action" label="动作" width="90" />
                <el-table-column prop="entityName" label="实体名" min-width="150" />
                <el-table-column prop="entityDisplayName" label="显示名" min-width="140" />
                <el-table-column prop="reason" label="原因" min-width="220" show-overflow-tooltip />
              </el-table>
            </section>
            <section class="guide-preview-panel">
              <div class="guide-panel-title">属性调整</div>
              <el-table :data="naturalAdjustPreview.propertyActions || []" border stripe size="small" max-height="240">
                <el-table-column prop="action" label="动作" width="90" />
                <el-table-column prop="entityName" label="所属实体" min-width="120" />
                <el-table-column prop="propertyName" label="属性名" min-width="150" />
                <el-table-column prop="propertyDisplayName" label="显示名" min-width="140" />
                <el-table-column prop="reason" label="原因" min-width="220" show-overflow-tooltip />
              </el-table>
            </section>
            <section class="guide-preview-panel">
              <div class="guide-panel-title">关系调整</div>
              <el-table :data="naturalAdjustPreview.relationActions || []" border stripe size="small" max-height="240">
                <el-table-column prop="action" label="动作" width="90" />
                <el-table-column prop="relationName" label="关系名" min-width="140" />
                <el-table-column label="方向" min-width="190">
                  <template #default="{ row }">
                    {{ row.sourceEntityName || '-' }} → {{ row.targetEntityName || '-' }}
                  </template>
                </el-table-column>
                <el-table-column prop="reason" label="原因" min-width="220" show-overflow-tooltip />
              </el-table>
            </section>
            <section v-if="naturalAdjustPreview.apply_result?.warnings?.length" class="guide-preview-panel">
              <div class="guide-panel-title">应用提示</div>
              <div class="natural-adjust-warnings">
                <div v-for="(warning, index) in naturalAdjustPreview.apply_result.warnings" :key="index">{{ warning }}</div>
              </div>
            </section>
          </div>
        </section>
      </div>
      <template #footer>
        <el-button @click="naturalAdjustDialogVisible = false">关闭</el-button>
        <el-button type="primary" plain :loading="naturalAdjustGenerating" @click="generateNaturalAdjustPlan">生成调整计划</el-button>
        <el-button type="success" :loading="naturalAdjustApplying" :disabled="!naturalAdjustPreview" @click="applyNaturalAdjustPlan">应用调整计划</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="processGuideDialogVisible" title="AI 辅助生成流程图" width="760px" destroy-on-close>
      <el-alert
        title="请选择流程描述类型并提供清晰的流程说明。生成结果可在创建后继续拖拽、连线和编辑。"
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom: 16px"
      />
      <el-form :model="processGuideForm" label-width="110px">
        <el-form-item label="流程类型">
          <el-radio-group v-model="processGuideForm.process_type">
            <el-radio-button value="DATA_ANALYSIS">数据分析流程</el-radio-button>
            <el-radio-button value="BUSINESS_PROCESS">业务处理流程</el-radio-button>
            <el-radio-button value="CUSTOM">自定义流程</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="流程描述" required>
          <el-input
            v-model="processGuideForm.process_description"
            type="textarea"
            :rows="8"
            maxlength="10000"
            show-word-limit
            placeholder="例如：从缺陷工单数据中筛选近30天高频缺陷，按设备和工序聚合，判断是否超过阈值；超过则创建整改任务并通知负责人，否则输出监控报告。"
          />
        </el-form-item>
        <el-form-item label="大模型配置">
          <el-select v-model="processGuideForm.model_config_id" clearable placeholder="留空则使用默认模型" style="width: 100%">
            <el-option v-for="model in guideModelOptions" :key="model.config_id" :label="formatGuideModelOption(model)" :value="model.config_id" />
          </el-select>
        </el-form-item>
      </el-form>
      <div v-if="processGuidePreview" class="guide-preview" style="margin-top: 16px">
        <div class="guide-preview-header">
          <strong>{{ processGuidePreview.process_name }}</strong>
          <el-tag size="small">{{ processGuidePreview.generation_mode === 'llm' ? '大模型生成' : '基础草案' }}</el-tag>
        </div>
        <p class="guide-preview-desc">{{ processGuidePreview.process_desc }}</p>
        <el-table :data="processGuidePreview.nodes || []" border stripe size="small" max-height="220">
          <el-table-column prop="label" label="流程节点" min-width="160" />
          <el-table-column prop="type" label="类型" width="110" />
          <el-table-column prop="desc" label="说明" min-width="260" show-overflow-tooltip />
        </el-table>
        <div class="guide-preview-summary">将创建 {{ processGuidePreview.nodes?.length || 0 }} 个节点、{{ processGuidePreview.edges?.length || 0 }} 条连线。</div>
      </div>
      <template #footer>
        <el-button @click="processGuideDialogVisible = false">取消</el-button>
        <el-button type="primary" plain :loading="processGuideGenerating" @click="generateProcessGuide">生成建议</el-button>
        <el-button type="success" :disabled="!processGuidePreview" :loading="processGuideApplying" @click="applyProcessGuide">创建并编辑流程图</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, onBeforeUnmount, computed, watch, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, UploadFilled } from '@element-plus/icons-vue'
import { entityApi, propertyApi, relationApi, graphApi, processApi, sourceApi, systemApi, domainApi, mappingApi, businessTypeApi } from '../../api'
import { useAppStore } from '../../stores/app'

interface GuideTableOption {
  owner: string
  table_name: string
  comments?: string | null
  num_rows?: number | null
}

interface GuideTableBinding {
  table_name: string
}

interface GuideDDLColumn {
  column_name: string
  data_type?: string | null
  nullable?: string | null
  comments?: string | null
  is_primary_key?: string | null
  column_id?: number | null
}

interface GuideDDLTable {
  owner?: string | null
  table_name: string
  table_comment?: string | null
  columns: GuideDDLColumn[]
}

interface GuideRuleDataset {
  rule_type: string
  table_name: string
  record_count: number
  columns: string[]
  records: Record<string, any>[]
  summary?: Record<string, any>
}

interface GuideModelOption {
  config_id: string
  config_name: string
  model_name: string
  is_active?: string
  is_default?: string
}

const route = useRoute()
const appStore = useAppStore()
const loading = ref(false)
const savingFlow = ref(false)
const fixedBuildSection = computed(() => route.meta?.buildSection as string | undefined)
const activeTab = ref(fixedBuildSection.value || 'graph')
const currentDomainId = ref(appStore.currentDomainId || '')
const currentDomainName = computed(() => appStore.currentDomainName || '')
const currentDomainDesc = ref('')
const currentDomainType = ref('BUSINESS')
const currentBusinessTypeName = ref('')
const currentBusinessTypeDesc = ref('')
const graphNodes = ref<any[]>([])
const graphEdges = ref<any[]>([])
const selectedNode = ref<any>(null)
const selectedNodeProperties = ref<any[]>([])
const processes = ref<any[]>([])
const canvasSize = reactive({ w: 1600, h: 800 })
const graphDragMoved = ref(false)
const graphConnecting = ref<any>(null)

const guideDialogVisible = ref(false)
const guideGenerating = ref(false)
const guideApplying = ref(false)
const guideSourceLoading = ref(false)
const guideSchemaLoading = ref(false)
const guideTableLoading = ref(false)
const guideUploadLoading = ref(false)
const guideDDLUploadLoading = ref(false)
const guideRuleUploadLoading = ref(false)
const guideDataSources = ref<any[]>([])
const guideSchemaOptions = ref<string[]>([])
const guideTables = ref<GuideTableOption[]>([])
const guideDDLSchemaTables = ref<GuideDDLTable[]>([])
const guideRuleDatasets = ref<GuideRuleDataset[]>([])
const guideModelOptions = ref<GuideModelOption[]>([])
const guideTableKeyword = ref('')
const guidePreview = ref<any>(null)
const guideRunState = ref<'idle' | 'running' | 'success' | 'warning' | 'error'>('idle')
const guideRunMessage = ref('')
const guideUploadedDocument = ref<{ file_name: string; char_count: number; file_type: string } | null>(null)
const guideUploadedDDLFiles = ref<Array<{ file_name: string; char_count: number; file_type: string; table_count: number }>>([])
const guideUploadedRuleFiles = ref<Array<{ file_name: string; char_count: number; file_type: string; dataset_count: number }>>([])
const guideStep = ref(1)
const guideStepOptions = [
  { value: 1, label: '资料输入', description: '上传问卷、DDL、规则数据，选择业务关系表与生成策略。' },
  { value: 2, label: '分析确认', description: '确认结构化分析出的场景、规则范围、关键表和重点站位。' },
  { value: 3, label: '本体预览', description: '检查 canonical 本体对象、关系和缺陷语义分类。' },
  { value: 4, label: '视图应用', description: '查看标准化视图计划、属性图骨架并准备应用。' },
]
const guidePatternOptions = ref<Array<{ value: string; label: string; description?: string }>>([])
const guideSemanticTypeOptions = ref<any[]>([])
const createEmptyGuideForm = () => ({
  table_source_mode: 'database' as 'database' | 'ddl',
  generation_strategy: 'structured_domain_pipeline',
  business_scenario: 'SFR_ROOTCAUSE',
  semantic_type_code: '',
  source_id: '',
  schema: '',
  relation_tables: [] as string[],
  rule_table_name: '',
  focus_metric_families: [] as string[],
  focus_stations: [] as string[],
  history_case_sources: [] as string[],
  enabled_patterns: [] as string[],
  business_document: '',
  model_config_id: '',
  sample_limit: 3,
  overwrite_existing: false
})
const guideForm = reactive(createEmptyGuideForm())
const handleGuideGenerationStrategyChange = (strategy: string) => {
  guideForm.business_scenario = strategy === 'llm_first'
    ? 'BUSINESS_SEMANTIC'
    : 'SFR_ROOTCAUSE'
  if (strategy === 'llm_first') {
    guideForm.semantic_type_code = guideForm.semantic_type_code || currentDomainType.value
    void loadGuidePatternOptions(guideForm.semantic_type_code, true)
  }
}
const selectedGuideTableBindings = computed<GuideTableBinding[]>(() =>
  guideForm.relation_tables.map(tableName => ({
    table_name: tableName
  }))
)
const activeGuideTables = computed<GuideTableOption[]>(() => (
  guideForm.table_source_mode === 'ddl'
    ? guideDDLSchemaTables.value.map((table: GuideDDLTable) => ({
      owner: table.owner || 'DDL',
      table_name: table.table_name,
      comments: table.table_comment || `${table.columns?.length || 0} 列`
    }))
    : guideTables.value
))
const selectedGuideDDLTables = computed<GuideDDLTable[]>(() => {
  const selectedSet = new Set(guideForm.relation_tables.map(item => item.toUpperCase()))
  return guideDDLSchemaTables.value.filter(table => selectedSet.has((table.table_name || '').toUpperCase()))
})
const filteredGuideTables = computed(() => {
  const keyword = guideTableKeyword.value.trim().toLowerCase()
  if (!keyword) return activeGuideTables.value
  return activeGuideTables.value.filter(table =>
    table.table_name.toLowerCase().includes(keyword) ||
    (table.comments || '').toLowerCase().includes(keyword) ||
    (table.owner || '').toLowerCase().includes(keyword)
  )
})
const activeGuideStepDescription = computed(() =>
  guideStepOptions.find(item => item.value === guideStep.value)?.description || ''
)
const isLlmFirstGuide = computed(() => guidePreview.value?.generation_strategy === 'llm_first')
const availableGuideHistorySources = computed(() => {
  const candidates = [
    ...(guidePreview.value?.document_facts?.history_knowledge_sources || []),
    ...(guidePreview.value?.focus_scope?.history_case_sources || []),
    ...(guidePreview.value?.schema_analysis?.key_tables?.history_case_tables || []),
  ]
  return Array.from(new Set(candidates.filter((item: string) => !!item)))
})

const processGuideDialogVisible = ref(false)
const processGuideGenerating = ref(false)
const processGuideApplying = ref(false)
const processGuidePreview = ref<any>(null)
const createEmptyProcessGuideForm = () => ({
  process_type: 'DATA_ANALYSIS',
  process_description: '',
  model_config_id: ''
})
const processGuideForm = reactive(createEmptyProcessGuideForm())

const naturalAdjustDialogVisible = ref(false)
const naturalAdjustGenerating = ref(false)
const naturalAdjustApplying = ref(false)
const naturalAdjustPreview = ref<any>(null)
const createEmptyNaturalAdjustForm = () => ({
  instruction: '',
  model_config_id: '',
  scope_selected_only: true
})
const naturalAdjustForm = reactive(createEmptyNaturalAdjustForm())

// ========== Graph Node Drag ==========
const graphSvgRef = ref<SVGElement | null>(null)
const draggingNode = ref<any>(null)
const dragOffset = reactive({ x: 0, y: 0 })
const graphNodeSize = { width: 160, height: 70 }
const graphArrowGap = 12
// The canvas grows with the lowest entity.  The surrounding viewport remains
// fixed and scrollable, so entities below the first screen are still reachable.
const graphCanvasHeight = computed(() => {
  const lowestNodeBottom = graphNodes.value.reduce((maxBottom, node) => {
    const position = node.position || { y: 0 }
    return Math.max(maxBottom, Number(position.y) || 0)
  }, 0) + graphNodeSize.height
  return Math.max(canvasSize.h, lowestNodeBottom + 100)
})

const getGraphCanvasPoint = (clientX: number, clientY: number) => {
  const svgRect = graphSvgRef.value?.getBoundingClientRect()
  if (!svgRect) return null
  const scaleX = canvasSize.w / svgRect.width
  const scaleY = graphCanvasHeight.value / svgRect.height
  return {
    x: (clientX - svgRect.left) * scaleX,
    y: (clientY - svgRect.top) * scaleY
  }
}

const getNodePos = (nodeId: string): { x: number; y: number } => {
  const node = graphNodes.value.find(n => n.id === nodeId)
  return node?.position || { x: 200, y: 200 }
}

const getNodeCenter = (nodeId: string) => {
  const pos = getNodePos(nodeId)
  return {
    x: pos.x + graphNodeSize.width / 2,
    y: pos.y + graphNodeSize.height / 2
  }
}

const isRelatedGraphEdge = (edge: any) => {
  if (!selectedNode.value) return false
  return edge.source === selectedNode.value.id || edge.target === selectedNode.value.id
}

const isRelatedGraphNode = (nodeId: string) => {
  if (!selectedNode.value || nodeId === selectedNode.value.id) return false
  return graphEdges.value.some(edge =>
    (edge.source === selectedNode.value.id && edge.target === nodeId)
    || (edge.target === selectedNode.value.id && edge.source === nodeId)
  )
}

const getNodeBoundaryPoint = (from: { x: number; y: number }, to: { x: number; y: number }, gap = 0) => {
  const halfWidth = graphNodeSize.width / 2 + gap
  const halfHeight = graphNodeSize.height / 2 + gap
  const dx = to.x - from.x
  const dy = to.y - from.y
  if (dx === 0 && dy === 0) {
    return { x: from.x + halfWidth, y: from.y }
  }

  const scale = 1 / Math.max(Math.abs(dx) / halfWidth, Math.abs(dy) / halfHeight)
  return {
    x: from.x + dx * scale,
    y: from.y + dy * scale
  }
}

const getGraphEdgeGeometry = (edge: any) => {
  const sourceCenter = getNodeCenter(edge.source)
  const targetCenter = getNodeCenter(edge.target)
  const start = getNodeBoundaryPoint(sourceCenter, targetCenter, 2)
  const end = getNodeBoundaryPoint(targetCenter, sourceCenter, graphArrowGap)
  return {
    start,
    end,
    mid: {
      x: (start.x + end.x) / 2,
      y: (start.y + end.y) / 2
    }
  }
}

const onNodeMouseDown = (e: MouseEvent, node: any) => {
  if (e.button !== 0) return // left click only
  draggingNode.value = node
  graphDragMoved.value = false
  const pos = getNodePos(node.id)
  const point = getGraphCanvasPoint(e.clientX, e.clientY)
  if (point) {
    dragOffset.x = point.x - pos.x
    dragOffset.y = point.y - pos.y
  }
  e.preventDefault()
}

const onGraphMouseMove = (e: MouseEvent) => {
  const point = getGraphCanvasPoint(e.clientX, e.clientY)
  if (graphConnecting.value && point) {
    graphConnecting.value.mouseX = point.x
    graphConnecting.value.mouseY = point.y
  }
  if (!draggingNode.value || !point) return
  const newX = Math.max(0, point.x - dragOffset.x)
  const newY = Math.max(0, point.y - dragOffset.y)
  if (Math.abs(newX - draggingNode.value.position.x) > 2 || Math.abs(newY - draggingNode.value.position.y) > 2) {
    graphDragMoved.value = true
  }
  draggingNode.value.position = { x: newX, y: newY }
}

const findGraphTargetNode = (x: number, y: number, excludeNodeId?: string) => {
  return graphNodes.value.find(node => {
    if (node.id === excludeNodeId) return false
    const pos = getNodePos(node.id)
    return x >= pos.x && x <= pos.x + 160 && y >= pos.y && y <= pos.y + 70
  })
}

const openCreateRelationDialog = (sourceNodeId: string, targetNodeId: string) => {
  relationDialogMode.value = 'create'
  editingRelationId.value = ''
  editingRelationTableName.value = ''
  relationForm.value = {
    source_entity_id: sourceNodeId,
    target_entity_id: targetNodeId,
    relation_name: '',
    relation_type: 'ASSOCIATION',
    relation_desc: '',
    relation_table_name: ''
  }
  relationDialogVisible.value = true
}

const startGraphConnection = async (e: MouseEvent, node: any) => {
  if (!graphSvgRef.value) return
  await selectNode(node)
  const pos = getNodePos(node.id)
  const point = getGraphCanvasPoint(e.clientX, e.clientY)
  graphConnecting.value = {
    sourceNodeId: node.id,
    startX: pos.x + 160,
    startY: pos.y + 35,
    mouseX: point?.x ?? pos.x + 160,
    mouseY: point?.y ?? pos.y + 35
  }
}

const onGraphMouseUp = (e: MouseEvent) => {
  const point = getGraphCanvasPoint(e.clientX, e.clientY)
  if (graphConnecting.value && point) {
    const mouseX = point.x
    const mouseY = point.y
    const targetNode = findGraphTargetNode(mouseX, mouseY, graphConnecting.value.sourceNodeId)
    const sourceNodeId = graphConnecting.value.sourceNodeId
    graphConnecting.value = null
    if (targetNode) {
      openCreateRelationDialog(sourceNodeId, targetNode.id)
    }
  }
  if (draggingNode.value) {
    draggingNode.value = null
  }
}

const onNodeClick = async (node: any) => {
  if (graphConnecting.value) return
  if (graphDragMoved.value) {
    graphDragMoved.value = false
    return
  }
  await selectNode(node)
}

// ========== Flow Editor vars ==========
const flowCanvasRef = ref<HTMLElement | null>(null)
const flowCanvasSize = reactive({ w: 1600, h: 800 })
const currentFlow = reactive<Record<string, any>>({})
const flowNodes = ref<any[]>([])
const flowEdges = ref<any[]>([])
const flowDragging = ref<any>(null)
const flowDragOffset = reactive({ x: 0, y: 0 })
const flowConfigNode = ref<any>(null)
const flowConnecting = ref<any>(null)
const flowSelectedNodeIds = ref<string[]>([])
const flowNodeCounter = ref(0)
const flowNodeSize = { width: 150, height: 60 }

const flowNodeTypes = [
  { type: 'start', label: '开始', icon: '▶', color: '#e8f5e9', borderColor: '#4caf50', fill: '#4caf50', stroke: '#388e3c', textColor: '#fff', rx: 30, ry: 30 },
  { type: 'dataInput', label: '数据输入', icon: '📥', color: '#e3f2fd', borderColor: '#2196f3', fill: '#2196f3', stroke: '#1976d2', textColor: '#fff', rx: 8, ry: 8 },
  { type: 'analysis', label: '分析节点', icon: '🔍', color: '#fff3e0', borderColor: '#ff9800', fill: '#ff9800', stroke: '#f57c00', textColor: '#fff', rx: 8, ry: 8 },
  { type: 'decision', label: '决策节点', icon: '🔀', color: '#fce4ec', borderColor: '#e91e63', fill: '#e91e63', stroke: '#c2185b', textColor: '#fff', rx: 4, ry: 4 },
  { type: 'action', label: '操作节点', icon: '⚡', color: '#f3e5f5', borderColor: '#9c27b0', fill: '#9c27b0', stroke: '#7b1fa2', textColor: '#fff', rx: 8, ry: 8 },
  { type: 'end', label: '结束', icon: '⏹', color: '#ffebee', borderColor: '#f44336', fill: '#f44336', stroke: '#d32f2f', textColor: '#fff', rx: 30, ry: 30 },
]

const getFlowNodeShape = (type: string) => {
  const found = flowNodeTypes.find(nt => nt.type === type)
  return found || flowNodeTypes[2]
}

// Node ID generator
let flowIdCounter = 0
const nextFlowNodeId = () => `fn_${++flowIdCounter}`

const extractFlowNumericId = (nodeId?: string) => {
  if (!nodeId) return 0
  const match = /^fn_(\d+)$/.exec(nodeId)
  return match ? Number(match[1]) : 0
}

const syncFlowIdCounter = (nodes: any[]) => {
  flowIdCounter = nodes.reduce((maxId: number, node: any) => Math.max(maxId, extractFlowNumericId(node?.id)), 0)
}

const normalizeFlowGraph = (parsed: any) => {
  const rawNodes = Array.isArray(parsed?.nodes) ? parsed.nodes : []
  const rawEdges = Array.isArray(parsed?.edges) ? parsed.edges : []
  flowIdCounter = rawNodes.reduce((maxId: number, node: any) => Math.max(maxId, extractFlowNumericId(node?.id)), 0)
  const seenIds = new Set<string>()
  const duplicateCount = new Map<string, number>()
  const normalizedNodes = rawNodes.map((node: any) => {
    const originalId = typeof node?.id === 'string' ? node.id : ''
    const duplicateIndex = duplicateCount.get(originalId) || 0
    let normalizedId = originalId

    if (!normalizedId || seenIds.has(normalizedId)) {
      normalizedId = nextFlowNodeId()
    }

    duplicateCount.set(originalId, duplicateIndex + 1)
    seenIds.add(normalizedId)

    return {
      ...node,
      id: normalizedId,
      typeName: flowNodeTypes.find(nt => nt.type === node.type)?.label || node.type,
      config: node.config || {}
    }
  })

  const validNodeIds = new Set(normalizedNodes.map((node: any) => node.id))
  const normalizedEdges = rawEdges.filter((edge: any) => {
    return edge?.source && edge?.target && validNodeIds.has(edge.source) && validNodeIds.has(edge.target)
  })

  syncFlowIdCounter(normalizedNodes)
  return {
    nodes: normalizedNodes,
    edges: normalizedEdges
  }
}

const layoutGeneratedFlowNodes = (nodes: any[]) => {
  return nodes.map((node: any, index: number) => {
    const column = index % 5
    const row = Math.floor(index / 5)
    return {
      ...node,
      position: { x: 40 + column * 180, y: 35 + row * 85 }
    }
  })
}

// ========== Node Position helpers ==========
const flowNodePos = (nodeId: string) => {
  const node = flowNodes.value.find(n => n.id === nodeId)
  return node?.position || { x: 200, y: 200 }
}

const getFlowNodeAnchor = (nodeId: string, side: 'left' | 'right') => {
  const pos = flowNodePos(nodeId)
  return {
    x: side === 'right' ? pos.x + flowNodeSize.width : pos.x,
    y: pos.y + flowNodeSize.height / 2
  }
}

const getFlowCurvePath = (start: { x: number; y: number }, end: { x: number; y: number }) => {
  const deltaX = Math.max(48, Math.abs(end.x - start.x) * 0.45)
  return `M ${start.x} ${start.y} C ${start.x + deltaX} ${start.y}, ${end.x - deltaX} ${end.y}, ${end.x} ${end.y}`
}

const getFlowEdgePath = (edge: any) => {
  const start = getFlowNodeAnchor(edge.source, 'right')
  const end = getFlowNodeAnchor(edge.target, 'left')
  return getFlowCurvePath(start, end)
}

const getFlowPreviewPath = (connecting: any) => {
  if (!connecting) return ''
  const start = connecting.fromAnchor || getFlowNodeAnchor(connecting.fromId, 'right')
  return getFlowCurvePath(start, { x: connecting.mouseX, y: connecting.mouseY })
}

const isFlowNodeSelected = (nodeId: string) => flowSelectedNodeIds.value.includes(nodeId)

const clearFlowSelection = () => {
  flowSelectedNodeIds.value = []
}

const syncFlowCanvasSize = () => {
  const canvasRect = flowCanvasRef.value?.getBoundingClientRect()
  if (!canvasRect) return
  flowCanvasSize.w = Math.max(1, Math.round(canvasRect.width))
  flowCanvasSize.h = Math.max(1, Math.round(canvasRect.height))
}

const getFlowCanvasPoint = (clientX: number, clientY: number) => {
  const canvasRect = flowCanvasRef.value?.getBoundingClientRect()
  if (!canvasRect) return null
  const scaleX = flowCanvasSize.w / canvasRect.width
  const scaleY = flowCanvasSize.h / canvasRect.height
  return {
    x: (clientX - canvasRect.left) * scaleX,
    y: (clientY - canvasRect.top) * scaleY
  }
}

// ========== Flow Drag ==========
const onFlowNodeMouseDown = (e: MouseEvent, node: any) => {
  if (e.button !== 0) return
  // Check if clicking delete/connect buttons
  const target = e.target as SVGElement
  if (target.tagName === 'circle' || target.tagName === 'text') return

  flowDragging.value = node
  const point = getFlowCanvasPoint(e.clientX, e.clientY)
  if (point) {
    flowDragOffset.x = point.x - node.position.x
    flowDragOffset.y = point.y - node.position.y
  }
  e.preventDefault()
}

const onFlowMouseMove = (e: MouseEvent) => {
  const point = getFlowCanvasPoint(e.clientX, e.clientY)
  if (flowConnecting.value && point) {
    flowConnecting.value.mouseX = point.x
    flowConnecting.value.mouseY = point.y
  }
  if (!flowDragging.value || !point) return
  flowDragging.value.position.x = Math.max(0, Math.min(flowCanvasSize.w - flowNodeSize.width, point.x - flowDragOffset.x))
  flowDragging.value.position.y = Math.max(0, Math.min(flowCanvasSize.h - flowNodeSize.height, point.y - flowDragOffset.y))
}

const hasFlowEdge = (sourceId: string, targetId: string) => {
  return flowEdges.value.some(edge => edge.source === sourceId && edge.target === targetId)
}

const appendFlowSelection = (nodeId: string) => {
  const existing = flowSelectedNodeIds.value.filter(id => id !== nodeId)
  flowSelectedNodeIds.value = [...existing, nodeId].slice(-2)
}

const findFlowTargetNode = (x: number, y: number, excludeNodeId?: string) => {
  const hitPadding = 22
  return [...flowNodes.value].reverse().find(node => {
    if (node.id === excludeNodeId) return false
    const nx = node.position.x
    const ny = node.position.y
    return (
      x >= nx - hitPadding &&
      x <= nx + flowNodeSize.width + hitPadding &&
      y >= ny - hitPadding &&
      y <= ny + flowNodeSize.height + hitPadding
    )
  })
}

const createFlowEdge = (sourceId: string, targetId: string, showMessage = true) => {
  if (!sourceId || !targetId) return false
  if (sourceId === targetId) {
    if (showMessage) ElMessage.warning('流程边不能连接到自身节点')
    return false
  }
  if (hasFlowEdge(sourceId, targetId)) {
    if (showMessage) ElMessage.warning('这两个节点之间的流程边已存在')
    return false
  }
  flowEdges.value.push({ source: sourceId, target: targetId })
  if (showMessage) ElMessage.success('流程边已创建')
  return true
}

const onFlowMouseUp = (e: MouseEvent) => {
  const point = getFlowCanvasPoint(e.clientX, e.clientY)
  if (flowConnecting.value && point) {
    const targetNode = findFlowTargetNode(point.x, point.y, flowConnecting.value.fromId)
    if (targetNode) {
      createFlowEdge(flowConnecting.value.fromId, targetNode.id)
      appendFlowSelection(flowConnecting.value.fromId)
      appendFlowSelection(targetNode.id)
    }
    flowConnecting.value = null
  }
  flowDragging.value = null
}

const onFlowNodeDragStart = (e: DragEvent, nt: any) => {
  e.dataTransfer?.setData('nodeType', nt.type)
}

const onFlowCanvasDragOver = (e: DragEvent) => {
  e.dataTransfer!.dropEffect = 'move'
}

const onFlowCanvasDrop = (e: DragEvent) => {
  const nodeType = e.dataTransfer?.getData('nodeType')
  if (!nodeType || !flowCanvasRef.value) return
  const point = getFlowCanvasPoint(e.clientX, e.clientY)
  if (!point) return
  const typeInfo = flowNodeTypes.find(nt => nt.type === nodeType)!
  const newNode: any = {
    id: nextFlowNodeId(),
    type: nodeType,
    typeName: typeInfo.label,
    label: typeInfo.label,
    desc: '',
    position: {
      x: Math.max(0, Math.min(flowCanvasSize.w - flowNodeSize.width, point.x - 75)),
      y: Math.max(0, Math.min(flowCanvasSize.h - flowNodeSize.height, point.y - 30))
    },
    config: {}
  }
  // Init default configs
  if (nodeType === 'dataInput') newNode.config = { ontologyEntity: '', usageMode: 'query', usageTime: '' }
  if (nodeType === 'analysis') newNode.config = { analysisType: '', skillId: '' }
  if (nodeType === 'action') newNode.config = { actionType: 'generateReport', template: '' }
  flowNodes.value.push(newNode)
  flowConfigNode.value = newNode
  appendFlowSelection(newNode.id)
}

const startFlowConnection = (e: MouseEvent, node: any) => {
  const point = getFlowCanvasPoint(e.clientX, e.clientY)
  const fromAnchor = getFlowNodeAnchor(node.id, 'right')
  flowConnecting.value = {
    fromId: node.id,
    fromAnchor,
    mouseX: point?.x ?? fromAnchor.x,
    mouseY: point?.y ?? fromAnchor.y
  }
  flowConfigNode.value = node
  appendFlowSelection(node.id)
  e.preventDefault()
}

const selectFlowNode = (node: any) => {
  flowConfigNode.value = node
  appendFlowSelection(node.id)
}

const deleteFlowNode = (idx: number) => {
  const nodeId = flowNodes.value[idx].id
  flowEdges.value = flowEdges.value.filter(e => e.source !== nodeId && e.target !== nodeId)
  flowNodes.value.splice(idx, 1)
  flowSelectedNodeIds.value = flowSelectedNodeIds.value.filter(id => id !== nodeId)
  if (flowConfigNode.value?.id === nodeId) flowConfigNode.value = null
}

const connectSelectedFlowNodes = () => {
  if (flowSelectedNodeIds.value.length !== 2) {
    ElMessage.warning('请先按顺序选中两个节点')
    return
  }
  const [sourceId, targetId] = flowSelectedNodeIds.value
  createFlowEdge(sourceId, targetId)
}

// ========== Load data ==========
const loadGraphData = async () => {
  if (!currentDomainId.value) return
  try {
    const res = await graphApi.getOntologyGraph(currentDomainId.value)
    graphNodes.value = res.data?.nodes || []
    graphEdges.value = res.data?.edges || []
    graphNodes.value.forEach((node, idx) => {
      if (!node.position || (node.position.x === 200 && node.position.y === 200)) {
        node.position = { x: 80 + idx * 200, y: 60 + (idx % 3) * 150 }
      }
    })
    selectedNode.value = null
    selectedNodeProperties.value = []
    graphConnecting.value = null
  } catch (e) {}
}

const selectNode = async (node: any) => {
  if (draggingNode.value) return
  selectedNode.value = node
  flowConfigNode.value = null
  try {
    const res = await propertyApi.list(node.id)
    selectedNodeProperties.value = res.data || []
  } catch (e) {}
}

const loadProcesses = async () => {
  if (!currentDomainId.value) return
  try { const res = await processApi.list(currentDomainId.value); processes.value = res.data || [] } catch (e) {}
}

const loadCurrentDomainDetail = async () => {
  if (!currentDomainId.value) {
    currentDomainDesc.value = ''
    currentDomainType.value = 'BUSINESS'
    currentBusinessTypeName.value = ''
    currentBusinessTypeDesc.value = ''
    guidePatternOptions.value = []
    guideSemanticTypeOptions.value = []
    return
  }
  try {
    const res = await domainApi.get(currentDomainId.value)
    currentDomainDesc.value = res.data?.domain_desc || ''
    currentDomainType.value = res.data?.domain_type || 'BUSINESS'
    if (!guideForm.semantic_type_code) guideForm.semantic_type_code = currentDomainType.value
    await Promise.all([loadGuideSemanticTypeOptions(), loadGuidePatternOptions(guideForm.semantic_type_code)])
  } catch (e) {
    currentDomainDesc.value = ''
    guidePatternOptions.value = []
  }
}

const loadGuideSemanticTypeOptions = async () => {
  const res = await businessTypeApi.list()
  guideSemanticTypeOptions.value = (res.data || []).filter((item: any) => item.status === 'ACTIVE')
}

const loadGuidePatternOptions = async (semanticTypeCode = guideForm.semantic_type_code || currentDomainType.value, selectAll = false) => {
  try {
    const res = await businessTypeApi.get(semanticTypeCode)
    currentBusinessTypeName.value = res.data?.type_name || semanticTypeCode
    currentBusinessTypeDesc.value = res.data?.semantic_desc || ''
    guidePatternOptions.value = (res.data?.semantic_patterns || []).map((pattern: any) => ({
      value: pattern.pattern_code,
      label: pattern.pattern_name,
      description: pattern.description || ''
    }))
    const available = new Set(guidePatternOptions.value.map(item => item.value))
    guideForm.enabled_patterns = guideForm.enabled_patterns.filter(item => available.has(item))
    if (selectAll) guideForm.enabled_patterns = guidePatternOptions.value.map(item => item.value)
  } catch (e) {
    currentBusinessTypeName.value = ''
    currentBusinessTypeDesc.value = ''
    guidePatternOptions.value = []
  }
}

const handleGuideSemanticTypeChange = async (semanticTypeCode: string) => {
  guideForm.enabled_patterns = []
  if (!semanticTypeCode) {
    currentBusinessTypeName.value = ''
    currentBusinessTypeDesc.value = ''
    guidePatternOptions.value = []
    return
  }
  await loadGuidePatternOptions(semanticTypeCode, true)
}

const guidePatternLabel = (patternCode: string) =>
  guidePatternOptions.value.find(item => item.value === patternCode)?.label || patternCode

const removeGuidePattern = (patternCode: string) => {
  guideForm.enabled_patterns = guideForm.enabled_patterns.filter(item => item !== patternCode)
}

const formatGuideModelOption = (model: GuideModelOption) => `${model.config_name} / ${model.model_name}`

const summarizePropertySources = (properties: any[]) => {
  const rows = (properties || [])
    .map((item: any) => {
      const propertyName = item?.propertyDisplayName || item?.propertyName || ''
      const sourceTable = item?.sourceTable || ''
      const sourceColumn = item?.sourceColumn || ''
      if (!propertyName || !sourceTable || !sourceColumn) return ''
      return `${propertyName}<- ${sourceTable}.${sourceColumn}`
    })
    .filter((item: string) => !!item)
  if (!rows.length) return '无'
  const preview = rows.slice(0, 3).join('；')
  return rows.length > 3 ? `${preview} 等 ${rows.length} 项` : preview
}

const summarizeRelationSource = (row: any) => {
  const sourceTable = row?.sourceTable || ''
  const targetTable = row?.targetTable || ''
  const hasEdgeSql = Boolean((row?.edgeSql || '').trim())
  const joinCondition = row?.joinCondition || ''
  const parts: string[] = []
  if (sourceTable) parts.push(`源表 ${sourceTable}`)
  if (targetTable && targetTable !== sourceTable) parts.push(`目标表 ${targetTable}`)
  if (joinCondition) parts.push(`Join ${joinCondition}`)
  if (hasEdgeSql) parts.push('已给出 edgeSql 草案')
  return parts.length ? parts.join('；') : '无'
}

const resetGuideForm = () => {
  const emptyForm = createEmptyGuideForm()
  Object.assign(guideForm, emptyForm)
  guideStep.value = 1
  if (currentDomainDesc.value) {
    guideForm.business_document = currentDomainDesc.value
  }
  guidePreview.value = null
  guideRunState.value = 'idle'
  guideRunMessage.value = ''
  guideTableKeyword.value = ''
  guideSchemaOptions.value = []
  guideTables.value = []
  guideDDLSchemaTables.value = []
  guideRuleDatasets.value = []
  guideUploadedDocument.value = null
  guideUploadedDDLFiles.value = []
  guideUploadedRuleFiles.value = []
}

const hasGuidePreviewContent = (payload: any) => {
  if (!payload || typeof payload !== 'object') return false
  return Array.isArray(payload.entities) || Array.isArray(payload.relations)
}

const canEnterGuideStep = (step: number) => {
  if (step <= 1) return true
  if (!guidePreview.value) return false
  if (step === 2) return isLlmFirstGuide.value || !!(guidePreview.value.document_facts || guidePreview.value.rule_analysis || guidePreview.value.schema_analysis)
  if (step === 3) return Array.isArray(guidePreview.value.entities) && guidePreview.value.entities.length > 0
  if (step === 4) return !!guidePreview.value.view_plan || !!guidePreview.value.deployment_design
  return false
}

const setGuideStep = (step: number) => {
  if (!canEnterGuideStep(step)) return
  guideStep.value = step
}

const moveGuideStep = (offset: number) => {
  const next = guideStep.value + offset
  if (!canEnterGuideStep(next)) return
  guideStep.value = next
}

const extractRequestErrorMessage = (error: any) => {
  const detail = error?.response?.data?.detail
  const message = error?.message || ''
  if (detail) return detail
  if (typeof message === 'string' && message.includes('timeout')) {
    return '前端等待超时，后台可能仍在继续生成。请稍后点击“重新加载预览”查看结果。'
  }
  return message || '请求失败'
}

const loadLatestGuidePreview = async () => {
  if (!currentDomainId.value) return null
  const previewRes = await mappingApi.getLatestBlueprint(currentDomainId.value)
  return previewRes.data || null
}

const handleGuideTableSourceModeChange = async () => {
  guideForm.relation_tables = []
  guideForm.rule_table_name = ''
  guideForm.enabled_patterns = []
  guideTableKeyword.value = ''
  if (guideForm.table_source_mode === 'database') {
    guideDDLSchemaTables.value = []
    guideRuleDatasets.value = []
    guideUploadedDDLFiles.value = []
    guideUploadedRuleFiles.value = []
  }
  if (guideForm.table_source_mode === 'database' && guideForm.source_id) {
    await loadGuideTables()
  }
}

const selectAllGuideTables = () => {
  guideForm.relation_tables = activeGuideTables.value.map(item => item.table_name)
}

const clearGuideTableSelection = () => {
  guideForm.relation_tables = []
}

const resetNaturalAdjustForm = () => {
  Object.assign(naturalAdjustForm, createEmptyNaturalAdjustForm())
  naturalAdjustForm.scope_selected_only = !!selectedNode.value
  naturalAdjustPreview.value = null
}

const loadGuideModels = async () => {
  try {
    const res = await systemApi.getLLMConfigs()
    guideModelOptions.value = (res.data || []).filter((item: GuideModelOption) => item.is_active === 'Y')
    if (!guideModelOptions.value.length) {
      guideForm.model_config_id = ''
      naturalAdjustForm.model_config_id = ''
      processGuideForm.model_config_id = ''
      return
    }
    const defaultModel = guideModelOptions.value.find(item => item.is_default === 'Y') || guideModelOptions.value[0]
    if (!guideForm.model_config_id || !guideModelOptions.value.some(item => item.config_id === guideForm.model_config_id)) {
      guideForm.model_config_id = defaultModel?.config_id || ''
    }
    if (!naturalAdjustForm.model_config_id || !guideModelOptions.value.some(item => item.config_id === naturalAdjustForm.model_config_id)) {
      naturalAdjustForm.model_config_id = defaultModel?.config_id || ''
    }
    if (processGuideForm.model_config_id && !guideModelOptions.value.some(item => item.config_id === processGuideForm.model_config_id)) {
      processGuideForm.model_config_id = ''
    }
  } catch (e) {
    guideModelOptions.value = []
  }
}

const loadGuideDataSources = async () => {
  if (!currentDomainId.value) return
  guideSourceLoading.value = true
  try {
    const res = await sourceApi.listDataSources(currentDomainId.value)
    guideDataSources.value = res.data || []
    if (!guideDataSources.value.length) {
      guideForm.source_id = ''
      guideSchemaOptions.value = []
      guideTables.value = []
      return
    }
    if (!guideDataSources.value.some(item => item.source_id === guideForm.source_id)) {
      guideForm.source_id = guideDataSources.value.find(item => item.is_default === 'Y')?.source_id || guideDataSources.value[0]?.source_id || ''
    }
    if (guideForm.source_id) {
      await loadGuideSchemas()
    }
  } catch (e) {
    guideDataSources.value = []
  } finally {
    guideSourceLoading.value = false
  }
}

const loadGuideSchemas = async () => {
  if (!guideForm.source_id) {
    guideSchemaOptions.value = []
    return
  }
  guideSchemaLoading.value = true
  try {
    const res = await sourceApi.getSchemas(guideForm.source_id)
    guideSchemaOptions.value = res.data?.schemas || []
    const defaultSchema = res.data?.default_schema || guideSchemaOptions.value[0] || ''
    if (!guideForm.schema || !guideSchemaOptions.value.includes(guideForm.schema)) {
      guideForm.schema = defaultSchema
    }
    await loadGuideTables()
  } catch (e) {
    guideSchemaOptions.value = []
    guideTables.value = []
  } finally {
    guideSchemaLoading.value = false
  }
}

const loadGuideTables = async () => {
  if (guideForm.table_source_mode === 'ddl') {
    guideTableLoading.value = false
    return
  }
  if (!guideForm.source_id) {
    guideTables.value = []
    return
  }
  guideTableLoading.value = true
  try {
    const res = await sourceApi.getRemoteTables(guideForm.source_id, { schema: guideForm.schema || undefined })
    guideTables.value = res.data?.tables || []
    guideForm.relation_tables = guideForm.relation_tables.filter(tableName =>
      guideTables.value.some(item => item.table_name === tableName)
    )
    if (guideForm.rule_table_name && !guideTables.value.some(item => item.table_name === guideForm.rule_table_name)) {
      guideForm.rule_table_name = ''
    }
  } catch (e) {
    guideTables.value = []
    guideForm.rule_table_name = ''
  } finally {
    guideTableLoading.value = false
  }
}

const handleGuideSourceChange = async () => {
  if (guideForm.table_source_mode === 'ddl') return
  guideForm.schema = ''
  guideForm.relation_tables = []
  guideForm.enabled_patterns = []
  guideSchemaOptions.value = []
  guideTables.value = []
  guideDDLSchemaTables.value = []
  guideForm.rule_table_name = ''
  guideRuleDatasets.value = []
  guideUploadedRuleFiles.value = []
  await loadGuideSchemas()
}

const handleGuideSchemaChange = async () => {
  if (guideForm.table_source_mode === 'ddl') return
  guideForm.relation_tables = []
  guideForm.rule_table_name = ''
  guideForm.enabled_patterns = []
  await loadGuideTables()
}

const openOntologyGuide = async () => {
  if (!currentDomainId.value) {
    ElMessage.warning('请先选择业务分析域')
    return
  }
  guideDialogVisible.value = true
  resetGuideForm()
  await Promise.all([loadCurrentDomainDetail(), loadGuideModels()])
  if (!guideForm.business_document && currentDomainDesc.value) {
    guideForm.business_document = currentDomainDesc.value
  }
  await loadGuideDataSources()
}

const openNaturalAdjustDialog = async () => {
  if (!currentDomainId.value) {
    ElMessage.warning('请先选择业务分析域')
    return
  }
  naturalAdjustDialogVisible.value = true
  resetNaturalAdjustForm()
  await Promise.all([loadCurrentDomainDetail(), loadGuideModels()])
}

const handleGuideDocumentFileChange = async (uploadFile: any) => {
  const file = uploadFile?.raw as File | undefined
  if (!file || !currentDomainId.value) return

  guideUploadLoading.value = true
  try {
    const res = await graphApi.parseOntologyGuideDocument(currentDomainId.value, file)
    const parsedText = (res.data?.text || '').trim()
    if (!parsedText) {
      ElMessage.warning('文档未解析出有效内容')
      return
    }
    guideForm.business_document = guideForm.business_document.trim()
      ? `${guideForm.business_document.trim()}\n\n${parsedText}`
      : parsedText
    guideUploadedDocument.value = {
      file_name: res.data?.file_name || file.name,
      char_count: Number(res.data?.char_count || parsedText.length),
      file_type: res.data?.file_type || ''
    }
    ElMessage.success(`文档已解析并写入说明框：${guideUploadedDocument.value.file_name}`)
  } catch (e) {
  } finally {
    guideUploadLoading.value = false
  }
}

const mergeGuideDDLSchemaTables = (tables: GuideDDLTable[]) => {
  const merged = new Map<string, GuideDDLTable>()
  for (const table of guideDDLSchemaTables.value) {
    merged.set((table.table_name || '').toUpperCase(), table)
  }
  for (const table of tables) {
    merged.set((table.table_name || '').toUpperCase(), table)
  }
  guideDDLSchemaTables.value = Array.from(merged.values())
}

const mergeGuideRuleDatasets = (datasets: GuideRuleDataset[]) => {
  const merged = new Map<string, GuideRuleDataset>()
  for (const dataset of guideRuleDatasets.value) {
    merged.set(`${dataset.rule_type}::${dataset.table_name}`.toUpperCase(), dataset)
  }
  for (const dataset of datasets) {
    merged.set(`${dataset.rule_type}::${dataset.table_name}`.toUpperCase(), dataset)
  }
  guideRuleDatasets.value = Array.from(merged.values())
}

const handleGuideDDLFileChange = async (uploadFile: any) => {
  const file = uploadFile?.raw as File | undefined
  if (!file || !currentDomainId.value) return

  guideDDLUploadLoading.value = true
  try {
    const res = await graphApi.parseOntologyGuideDDL(currentDomainId.value, file)
    const parsedTables = (res.data?.tables || []) as GuideDDLTable[]
    if (!parsedTables.length) {
      ElMessage.warning('DDL 文件未解析出有效表结构')
      return
    }
    mergeGuideDDLSchemaTables(parsedTables)
    guideUploadedDDLFiles.value = [
      ...guideUploadedDDLFiles.value.filter(item => item.file_name !== (res.data?.file_name || file.name)),
      {
      file_name: res.data?.file_name || file.name,
      char_count: Number(res.data?.char_count || 0),
      file_type: res.data?.file_type || '',
      table_count: Number(res.data?.table_count || parsedTables.length),
      }
    ]
    guideForm.relation_tables = guideDDLSchemaTables.value.map(table => table.table_name)
    ElMessage.success(`DDL文件已解析：当前累计 ${guideUploadedDDLFiles.value.length} 个文件、${guideDDLSchemaTables.value.length} 张表`)
  } catch (e) {
  } finally {
    guideDDLUploadLoading.value = false
  }
}

const handleGuideRuleFileChange = async (uploadFile: any) => {
  const file = uploadFile?.raw as File | undefined
  if (!file || !currentDomainId.value) return

  guideRuleUploadLoading.value = true
  try {
    const res = await graphApi.parseOntologyGuideRuleData(currentDomainId.value, file)
    const parsedDatasets = (res.data?.datasets || []) as GuideRuleDataset[]
    if (!parsedDatasets.length) {
      ElMessage.warning('规则数据文件未解析出可用规则')
      return
    }
    mergeGuideRuleDatasets(parsedDatasets)
    guideUploadedRuleFiles.value = [
      ...guideUploadedRuleFiles.value.filter(item => item.file_name !== (res.data?.file_name || file.name)),
      {
        file_name: res.data?.file_name || file.name,
        char_count: Number(res.data?.char_count || 0),
        file_type: res.data?.file_type || '',
        dataset_count: Number(res.data?.dataset_count || parsedDatasets.length),
      }
    ]
    ElMessage.success(`规则数据已解析：当前累计 ${guideUploadedRuleFiles.value.length} 个文件、${guideRuleDatasets.value.length} 组规则数据`)
  } catch (e) {
  } finally {
    guideRuleUploadLoading.value = false
  }
}

const generateOntologyGuide = async (autoApply = false) => {
  if (!currentDomainId.value) {
    ElMessage.warning('请先选择业务分析域')
    return
  }
  if (guideForm.table_source_mode === 'database' && !guideForm.source_id) {
    ElMessage.warning('请选择数据库连接')
    return
  }
  if (guideForm.table_source_mode === 'ddl' && !guideDDLSchemaTables.value.length) {
    ElMessage.warning('请先上传并解析数据库DDL文件')
    return
  }
  if (!guideForm.relation_tables.length) {
    ElMessage.warning('请至少选择一张业务关系表')
    return
  }
  if (!guideForm.business_document.trim()) {
    ElMessage.warning('请输入业务说明文档')
    return
  }

  const runner = autoApply ? guideApplying : guideGenerating
  runner.value = true
  guideRunState.value = 'running'
  guideRunMessage.value = autoApply ? '正在生成并应用结果，请稍候…' : '正在生成建议，请稍候…'
  try {
    const res = await graphApi.generateOntologyGuide(currentDomainId.value, {
      generation_strategy: guideForm.generation_strategy,
      business_scenario: guideForm.business_scenario || null,
      semantic_type_code: guideForm.generation_strategy === 'llm_first' ? (guideForm.semantic_type_code || null) : null,
      source_id: guideForm.table_source_mode === 'database' ? guideForm.source_id : null,
      schema: guideForm.table_source_mode === 'database' ? (guideForm.schema || null) : null,
      table_source_mode: guideForm.table_source_mode,
      relation_tables: guideForm.relation_tables,
      rule_table_name: guideForm.table_source_mode === 'database' ? (guideForm.rule_table_name || null) : null,
      table_bindings: selectedGuideTableBindings.value,
      ddl_tables: guideForm.table_source_mode === 'ddl' ? selectedGuideDDLTables.value : [],
      rule_datasets: guideRuleDatasets.value,
      focus_metric_families: guideForm.focus_metric_families,
      focus_stations: guideForm.focus_stations,
      history_case_sources: guideForm.history_case_sources,
      enabled_patterns: guideForm.enabled_patterns,
      business_document: guideForm.business_document,
      model_config_id: guideForm.model_config_id || null,
      sample_limit: guideForm.sample_limit,
      auto_apply: autoApply,
      overwrite_existing: guideForm.overwrite_existing,
    })
    const postPayload = res.data || {}
    if (hasGuidePreviewContent(postPayload)) {
      guidePreview.value = {
        ...postPayload,
        apply_result: postPayload?.apply_result || null,
      }
    }

    try {
      const latestPreview = await loadLatestGuidePreview()
      if (hasGuidePreviewContent(latestPreview)) {
        guidePreview.value = {
          ...latestPreview,
          apply_result: res.data?.apply_result || null,
        }
        if (canEnterGuideStep(2)) {
          guideStep.value = 2
        }
        guideRunState.value = 'success'
        guideRunMessage.value = `已生成预览：实体 ${guidePreview.value?.entities?.length || 0} 个，关系 ${guidePreview.value?.relations?.length || 0} 条。`
      } else if (!hasGuidePreviewContent(guidePreview.value)) {
        guideRunState.value = 'warning'
        guideRunMessage.value = '已生成成功，但最新预览结果为空，请稍后重试刷新预览。'
        ElMessage.warning('已生成成功，但最新预览结果为空，请稍后重试刷新预览')
      }
    } catch (previewError) {
      console.error('loadLatestGuidePreview failed', previewError)
      if (!hasGuidePreviewContent(guidePreview.value)) {
        guideRunState.value = 'warning'
        guideRunMessage.value = `已生成成功，但读取预览失败：${extractRequestErrorMessage(previewError)}`
        ElMessage.warning('已生成成功，但读取预览失败，请稍后重试')
      } else {
        guideRunState.value = 'warning'
        guideRunMessage.value = '已生成成功，当前显示的是接口直接返回的预览；数据库预览刷新失败。'
      }
    }

    if (hasGuidePreviewContent(guidePreview.value) && guideRunState.value === 'running') {
      if (canEnterGuideStep(2)) {
        guideStep.value = 2
      }
      guideRunState.value = 'success'
      guideRunMessage.value = `已生成预览：实体 ${guidePreview.value?.entities?.length || 0} 个，关系 ${guidePreview.value?.relations?.length || 0} 条。`
    }

    if (autoApply) {
      const applyResult = guidePreview.value?.apply_result
      guideRunState.value = 'success'
      guideRunMessage.value = `已应用生成结果：新增实体 ${applyResult?.entities?.created || 0} 个，新增关系 ${applyResult?.relations?.created || 0} 条。`
      ElMessage.success(
        `已应用生成结果：新增实体 ${applyResult?.entities?.created || 0} 个，新增关系 ${applyResult?.relations?.created || 0} 条`
      )
      await loadGraphData()
    } else {
      ElMessage.success(
        hasGuidePreviewContent(guidePreview.value)
          ? 'Guide 生成完成，请先检查实体和关系建议后再应用'
          : 'Guide 已生成成功，但当前未拿到可展示预览，请稍后重试'
      )
    }
  } catch (e) {
    console.error('generateOntologyGuide failed', e)
    guideRunState.value = 'error'
    guideRunMessage.value = `生成失败：${extractRequestErrorMessage(e)}`
  } finally {
    runner.value = false
  }
}

const reloadGuidePreview = async () => {
  if (!currentDomainId.value) return
  guideGenerating.value = true
  guideRunState.value = 'running'
  guideRunMessage.value = '正在读取最新预览…'
  try {
    const latestPreview = await loadLatestGuidePreview()
    if (hasGuidePreviewContent(latestPreview)) {
      guidePreview.value = latestPreview
      if (canEnterGuideStep(2)) {
        guideStep.value = Math.max(guideStep.value, 2)
      }
      guideRunState.value = 'success'
      guideRunMessage.value = `已读取最新预览：实体 ${guidePreview.value?.entities?.length || 0} 个，关系 ${guidePreview.value?.relations?.length || 0} 条。`
      ElMessage.success('已刷新最新预览')
    } else {
      guideRunState.value = 'warning'
      guideRunMessage.value = '当前最新 blueprint 中没有可展示的实体或关系。'
      ElMessage.warning('当前最新 blueprint 中没有可展示的实体或关系')
    }
  } catch (e) {
    console.error('reloadGuidePreview failed', e)
    guideRunState.value = 'error'
    guideRunMessage.value = `读取预览失败：${extractRequestErrorMessage(e)}`
  } finally {
    guideGenerating.value = false
  }
}

const generateOntologyGuidePreview = async () => {
  await generateOntologyGuide(false)
}

const applyOntologyGuide = async () => {
  if (!currentDomainId.value) {
    ElMessage.warning('请先选择业务分析域')
    return
  }
  if (!guidePreview.value?.entities?.length) {
    ElMessage.warning('当前没有可应用的 Guide 预览结果')
    return
  }
  guideApplying.value = true
  guideRunState.value = 'running'
  guideRunMessage.value = '正在应用当前预览结果，请稍候…'
  try {
    const res = await graphApi.applyOntologyGuide(currentDomainId.value, {
      blueprint_id: guidePreview.value?.blueprint_id || null,
      blueprint: {
        entities: guidePreview.value?.entities || [],
        relations: guidePreview.value?.relations || [],
      },
      overwrite_existing: guideForm.overwrite_existing,
    })
    guidePreview.value = {
      ...guidePreview.value,
      blueprint_status: 'APPLIED',
      apply_result: res.data?.apply_result,
    }
    guideRunState.value = 'success'
    guideRunMessage.value = `已应用当前预览：新增实体 ${res.data?.apply_result?.entities?.created || 0} 个，新增关系 ${res.data?.apply_result?.relations?.created || 0} 条。`
    await loadGraphData()
    ElMessage.success(
      `已应用当前预览：新增实体 ${res.data?.apply_result?.entities?.created || 0} 个，新增关系 ${res.data?.apply_result?.relations?.created || 0} 条`
    )
  } catch (e) {
    console.error('applyOntologyGuide failed', e)
    guideRunState.value = 'error'
    guideRunMessage.value = `应用失败：${extractRequestErrorMessage(e)}`
  } finally {
    guideApplying.value = false
  }
}

const generateNaturalAdjustPlan = async () => {
  if (!currentDomainId.value) {
    ElMessage.warning('请先选择业务分析域')
    return
  }
  if (!naturalAdjustForm.instruction.trim()) {
    ElMessage.warning('请输入调整说明')
    return
  }
  naturalAdjustGenerating.value = true
  try {
    const res = await graphApi.naturalAdjustOntology(currentDomainId.value, {
      instruction: naturalAdjustForm.instruction,
      selected_entity_id: naturalAdjustForm.scope_selected_only ? selectedNode.value?.id || null : null,
      model_config_id: naturalAdjustForm.model_config_id || null,
      auto_apply: false
    })
    naturalAdjustPreview.value = res.data
    ElMessage.success('调整计划已生成，请检查后再应用')
  } catch (e) {
  } finally {
    naturalAdjustGenerating.value = false
  }
}

const applyNaturalAdjustPlan = async () => {
  if (!currentDomainId.value || !naturalAdjustPreview.value) return
  naturalAdjustApplying.value = true
  const previousSelectedId = selectedNode.value?.id || ''
  try {
    const res = await graphApi.applyNaturalAdjustOntology(currentDomainId.value, {
      plan: naturalAdjustPreview.value
    })
    naturalAdjustPreview.value = {
      ...naturalAdjustPreview.value,
      apply_result: res.data?.apply_result
    }
    await loadGraphData()
    const nextSelectedId = naturalAdjustForm.scope_selected_only ? previousSelectedId : ''
    if (nextSelectedId) {
      const refreshedNode = graphNodes.value.find(node => node.id === nextSelectedId)
      if (refreshedNode) {
        await selectNode(refreshedNode)
      }
    }
    const applyResult = res.data?.apply_result
    ElMessage.success(
      `已应用调整：对象 新增${applyResult?.entities?.created || 0}、更新${applyResult?.entities?.updated || 0}、删除${applyResult?.entities?.deleted || 0}；属性 新增${applyResult?.properties?.created || 0}、更新${applyResult?.properties?.updated || 0}、删除${applyResult?.properties?.deleted || 0}；关系 新增${applyResult?.relations?.created || 0}、更新${applyResult?.relations?.updated || 0}、删除${applyResult?.relations?.deleted || 0}`
    )
  } catch (e) {
  } finally {
    naturalAdjustApplying.value = false
  }
}

const loadActiveSectionData = async () => {
  if (!currentDomainId.value) return
  if (activeTab.value === 'graph') {
    await loadGraphData()
  } else if (activeTab.value === 'flow') {
    await loadProcesses()
  }
}

const resetDomainState = () => {
  currentFlow.process_id = undefined
  selectedNode.value = null
  selectedNodeProperties.value = []
  graphNodes.value = []
  graphEdges.value = []
  processes.value = []
  flowConfigNode.value = null
  guideDialogVisible.value = false
  guidePreview.value = null
  guideDataSources.value = []
  guideSchemaOptions.value = []
  guideTables.value = []
  naturalAdjustDialogVisible.value = false
  naturalAdjustPreview.value = null
  currentDomainDesc.value = ''
  currentDomainType.value = 'BUSINESS'
  currentBusinessTypeName.value = ''
  currentBusinessTypeDesc.value = ''
  guidePatternOptions.value = []
  guideSemanticTypeOptions.value = []
}

// ========== Dialogs ==========
const createEmptyEntityForm = () => ({ entity_name: '', entity_display_name: '', entity_desc: '', build_type: 'TABLE', color: '#66bb6a' })
const createEmptyRelationForm = () => ({ source_entity_id: '', target_entity_id: '', relation_name: '', relation_type: 'ASSOCIATION', relation_desc: '', relation_table_name: '' })
const createEmptyPropertyForm = () => ({ property_name: '', property_display_name: '', data_type: 'VARCHAR2', is_primary_key: 'N', is_nullable: 'Y', property_desc: '' })

const showAddEntity = () => {
  entityDialogMode.value = 'create'
  editingEntityId.value = ''
  entityForm.value = createEmptyEntityForm()
  entityDialogVisible.value = true
}
const showAddRelation = () => {
  relationDialogMode.value = 'create'
  editingRelationId.value = ''
  editingRelationTableName.value = ''
  relationForm.value = createEmptyRelationForm()
  relationDialogVisible.value = true
}
const showAddProperty = () => {
  propertyDialogMode.value = 'create'
  editingPropertyId.value = ''
  propertyForm.value = createEmptyPropertyForm()
  propertyDialogVisible.value = true
}
const showEditProperty = (prop: any) => {
  propertyDialogMode.value = 'edit'
  editingPropertyId.value = prop.property_id
  propertyForm.value = {
    property_name: prop.property_name || '',
    property_display_name: prop.property_display_name || '',
    data_type: prop.data_type || 'VARCHAR2',
    is_primary_key: prop.is_primary_key || 'N',
    is_nullable: prop.is_nullable || 'Y',
    property_desc: prop.property_desc || ''
  }
  propertyDialogVisible.value = true
}
const showCreateProcess = () => { processDialogVisible.value = true }

const openEntityEditor = (node: any) => {
  if (!node) return
  entityDialogMode.value = 'edit'
  editingEntityId.value = node.id
  entityForm.value = {
    entity_name: node.name || '',
    entity_display_name: node.displayName || '',
    entity_desc: node.desc || '',
    build_type: node.buildType || 'TABLE',
    color: node.color || '#66bb6a'
  }
  entityDialogVisible.value = true
}

const openRelationEditor = (edge: any) => {
  if (!edge) return
  relationDialogMode.value = 'edit'
  editingRelationId.value = edge.id
  editingRelationTableName.value = edge.relationTableName || ''
  relationForm.value = {
    source_entity_id: edge.source || '',
    target_entity_id: edge.target || '',
    relation_name: edge.name || '',
    relation_type: edge.type || 'ASSOCIATION',
    relation_desc: edge.desc || '',
    relation_table_name: edge.relationTableName || ''
  }
  relationDialogVisible.value = true
}

const saveEntity = async () => {
  if (!currentDomainId.value) { ElMessage.warning('请先选择业务分析域'); return }
  loading.value = true
  try {
    if (entityDialogMode.value === 'edit' && editingEntityId.value) {
      await entityApi.update(editingEntityId.value, entityForm.value)
      ElMessage.success('实体已更新')
    } else {
      await entityApi.create(currentDomainId.value, entityForm.value)
      ElMessage.success('实体创建成功')
    }
    entityDialogVisible.value = false
    entityForm.value = createEmptyEntityForm()
    await loadGraphData()
    if (entityDialogMode.value === 'edit') {
      const refreshedNode = graphNodes.value.find(node => node.id === editingEntityId.value)
      if (refreshedNode) {
        await selectNode(refreshedNode)
      }
    }
  } catch (e) {} finally { loading.value = false }
}

const deleteEntity = async () => {
  if (!selectedNode.value) return
  try {
    await ElMessageBox.confirm(
      `确定删除实体「${selectedNode.value.displayName || selectedNode.value.name}」及其属性、关联关系吗？`,
      '确认删除',
      { type: 'warning' }
    )
  } catch { return }
  try {
    const res = await entityApi.delete(selectedNode.value.id)
    const deletedRelationCount = res.data?.deleted_relation_count || 0
    ElMessage.success(
      deletedRelationCount > 0
        ? `实体已删除，同时删除 ${deletedRelationCount} 条关联关系`
        : '实体已删除'
    )
    selectedNode.value = null
    selectedNodeProperties.value = []
    await loadGraphData()
  } catch (e) {}
}

const clearOntologyData = async () => {
  if (!currentDomainId.value) {
    ElMessage.warning('请先选择业务分析域')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确定清空分析域「${currentDomainName.value || currentDomainId.value}」下的全部本体实体、属性、关系、映射、Guide设计包以及已生成的DDL日志/物理对象吗？此操作不可恢复，不会删除流程图、数据源和业务分析域本身。`,
      '确认清空本体与DDL数据',
      {
        type: 'warning',
        confirmButtonText: '确认清空',
        cancelButtonText: '取消'
      }
    )
  } catch {
    return
  }

  loading.value = true
  try {
    const res = await graphApi.clearOntologyData(currentDomainId.value)
    entityDialogVisible.value = false
    relationDialogVisible.value = false
    propertyDialogVisible.value = false
    selectedNode.value = null
    selectedNodeProperties.value = []
    graphConnecting.value = null
    editingEntityId.value = ''
    editingRelationId.value = ''
    editingRelationTableName.value = ''
    entityForm.value = createEmptyEntityForm()
    relationForm.value = createEmptyRelationForm()
    propertyForm.value = createEmptyPropertyForm()
    naturalAdjustPreview.value = null
    await loadGraphData()
    ElMessage.success(
      `已清空：实体 ${res.data?.deleted_entities || 0} 个、属性 ${res.data?.deleted_properties || 0} 个、关系 ${res.data?.deleted_relations || 0} 条、实体映射 ${res.data?.deleted_entity_mappings || 0} 个、属性映射 ${res.data?.deleted_property_mappings || 0} 个、关系映射 ${res.data?.deleted_relation_mappings || 0} 个、设计包 ${res.data?.deleted_blueprints || 0} 个、映射任务 ${res.data?.deleted_mapping_tasks || 0} 个、DDL日志 ${res.data?.deleted_ddl_logs || 0} 个、执行明细 ${res.data?.deleted_ddl_statement_logs || 0} 条；已删除图 ${res.data?.dropped_graphs || 0} 个、视图 ${res.data?.dropped_views || 0} 个、表 ${res.data?.dropped_tables || 0} 个`
    )
  } catch (e) {
  } finally {
    loading.value = false
  }
}

const saveRelation = async () => {
  if (!currentDomainId.value) { ElMessage.warning('请先选择业务分析域'); return }
  loading.value = true
  try {
    if (relationDialogMode.value === 'edit' && editingRelationId.value) {
      await relationApi.update(editingRelationId.value, relationForm.value)
      ElMessage.success('关系已更新')
    } else {
      await relationApi.create(currentDomainId.value, relationForm.value)
      ElMessage.success('关系创建成功')
    }
    relationDialogVisible.value = false
    relationForm.value = createEmptyRelationForm()
    editingRelationId.value = ''
    editingRelationTableName.value = ''
    await loadGraphData()
  } catch (e) {} finally { loading.value = false }
}

const deleteRelation = async () => {
  if (!editingRelationId.value) return
  try {
    await ElMessageBox.confirm(`确定删除关系「${relationForm.value.relation_name || '未命名关系'}」?`, '确认删除', { type: 'warning' })
  } catch { return }

  loading.value = true
  try {
    await relationApi.delete(editingRelationId.value)
    ElMessage.success('关系已删除')
    relationDialogVisible.value = false
    relationForm.value = createEmptyRelationForm()
    editingRelationId.value = ''
    editingRelationTableName.value = ''
    await loadGraphData()
  } catch (e) {} finally { loading.value = false }
}

const saveProperty = async () => {
  if (!selectedNode.value) { ElMessage.warning('请先选择实体'); return }
  loading.value = true
  try {
    if (propertyDialogMode.value === 'edit' && editingPropertyId.value) {
      await propertyApi.update(editingPropertyId.value, propertyForm.value)
      ElMessage.success('属性已更新')
    } else {
      await propertyApi.create(selectedNode.value.id, propertyForm.value)
      ElMessage.success('属性创建成功')
    }
    propertyDialogVisible.value = false
    propertyDialogMode.value = 'create'
    editingPropertyId.value = ''
    propertyForm.value = createEmptyPropertyForm()
    await selectNode(selectedNode.value)
    await loadGraphData()
  } catch (e) {} finally { loading.value = false }
}

const deleteProperty = async (propertyId: string) => {
  try {
    await propertyApi.delete(propertyId)
    ElMessage.success('属性已删除')
    await selectNode(selectedNode.value)
    await loadGraphData()
  } catch (e) {}
}

const saveAllPositions = async () => {
  for (const node of graphNodes.value) {
    try { await entityApi.updatePosition(node.id, node.position) } catch (e) {}
  }
  ElMessage.success('所有实体位置已保存')
}

// ========== Flow Operations ==========
const openFlowEditor = (proc: any) => {
  Object.assign(currentFlow, proc)
  flowIdCounter = 0
  if (proc.process_json) {
    try {
      const parsed = typeof proc.process_json === 'string' ? JSON.parse(proc.process_json) : proc.process_json
      const normalized = normalizeFlowGraph(parsed)
      // 兼容此前 AI 生成的单行流程：重新排成紧凑网格，避免右侧节点被固定画布裁切。
      flowNodes.value = parsed?.generatedBy ? layoutGeneratedFlowNodes(normalized.nodes) : normalized.nodes
      flowEdges.value = normalized.edges
    } catch {
      flowNodes.value = []
      flowEdges.value = []
    }
  } else {
    flowNodes.value = [
      { id: nextFlowNodeId(), type: 'start', typeName: '开始', label: '开始', desc: '', position: { x: 100, y: 250 }, config: {} },
      { id: nextFlowNodeId(), type: 'end', typeName: '结束', label: '结束', desc: '', position: { x: 700, y: 250 }, config: {} }
    ]
    flowEdges.value = [{ source: flowNodes.value[0].id, target: flowNodes.value[1].id }]
  }
  syncFlowIdCounter(flowNodes.value)
  flowConfigNode.value = null
  clearFlowSelection()
  flowConnecting.value = null
  flowDragging.value = null
  nextTick(() => {
    syncFlowCanvasSize()
  })
}

const closeFlowEditor = () => {
  currentFlow.process_id = undefined
  flowNodes.value = []
  flowEdges.value = []
  flowConfigNode.value = null
  clearFlowSelection()
  flowConnecting.value = null
  flowDragging.value = null
}

const saveFlowToServer = async () => {
  savingFlow.value = true
  const processJson = {
    processId: currentFlow.process_id,
    processName: currentFlow.process_name,
    domainId: currentDomainId.value,
    nodes: flowNodes.value.map(n => ({
      id: n.id, type: n.type, position: n.position, label: n.label, desc: n.desc, config: n.config
    })),
    edges: flowEdges.value.map(e => ({ source: e.source, target: e.target })),
    version: currentFlow.version || '1.0',
    createdAt: currentFlow.created_at || new Date().toISOString(),
    createdBy: currentFlow.created_by || appStore.user?.username
  }
  try {
    await processApi.update(currentFlow.process_id, { process_json: JSON.stringify(processJson), process_name: currentFlow.process_name })
    ElMessage.success('流程已保存')
  } catch (e) {} finally { savingFlow.value = false }
}

const resetFlowFromServer = async () => {
  if (currentFlow.process_json) {
    try {
      const parsed = typeof currentFlow.process_json === 'string' ? JSON.parse(currentFlow.process_json) : currentFlow.process_json
      const normalized = normalizeFlowGraph(parsed)
      flowNodes.value = normalized.nodes
      flowEdges.value = normalized.edges
      clearFlowSelection()
      flowConfigNode.value = null
      ElMessage.success('已重置为服务器版本')
    } catch { ElMessage.error('解析失败') }
  }
}

const deleteFlowProcess = async () => {
  try { await ElMessageBox.confirm('确定删除此流程?', '确认', { type: 'warning' }) } catch { return }
  try {
    await processApi.delete(currentFlow.process_id!)
    ElMessage.success('流程已删除')
    closeFlowEditor()
    await loadProcesses()
  } catch (e) {}
}

const deleteProcessItem = async (processId: string) => {
  try { await ElMessageBox.confirm('确定删除此流程?', '确认', { type: 'warning' }) } catch { return }
  try { await processApi.delete(processId); ElMessage.success('已删除'); await loadProcesses() } catch (e) {}
}

const createProcess = async () => {
  if (!currentDomainId.value) { ElMessage.warning('请先选择业务分析域'); return }
  loading.value = true
  try {
    flowIdCounter = 0
    const defaultFlow = {
      nodes: [
        { id: nextFlowNodeId(), type: 'start', position: { x: 100, y: 250 }, label: '开始', config: {} },
        { id: nextFlowNodeId(), type: 'end', position: { x: 700, y: 250 }, label: '结束', config: {} }
      ],
      edges: [{ source: '', target: '' }]
    }
    // Fix edges
    defaultFlow.edges[0].source = defaultFlow.nodes[0].id
    defaultFlow.edges[0].target = defaultFlow.nodes[1].id
    const res = await processApi.create(currentDomainId.value, {
      process_name: processForm.value.process_name,
      process_desc: processForm.value.process_desc,
      process_json: JSON.stringify(defaultFlow)
    })
    ElMessage.success('分析流程图创建成功')
    processDialogVisible.value = false
    processForm.value = { process_name: '', process_desc: '' }
    await loadProcesses()
    if (res.data) {
      openFlowEditor(res.data)
    }
  } catch (e) {} finally { loading.value = false }
}

const openProcessGuide = async () => {
  if (!currentDomainId.value) { ElMessage.warning('请先选择业务分析域'); return }
  Object.assign(processGuideForm, createEmptyProcessGuideForm())
  processGuidePreview.value = null
  if (currentDomainDesc.value) processGuideForm.process_description = currentDomainDesc.value
  processGuideDialogVisible.value = true
  if (!guideModelOptions.value.length) await loadGuideModels()
}

const generateProcessGuide = async () => {
  if (!currentDomainId.value) { ElMessage.warning('请先选择业务分析域'); return }
  if (!processGuideForm.process_description.trim()) { ElMessage.warning('请提供流程描述'); return }
  processGuideGenerating.value = true
  try {
    const res = await processApi.generateGuide(currentDomainId.value, {
      process_type: processGuideForm.process_type,
      process_description: processGuideForm.process_description,
      model_config_id: processGuideForm.model_config_id || null
    })
    processGuidePreview.value = res.data
    ElMessage.success('流程建议已生成，请确认后创建')
  } catch (e) {} finally { processGuideGenerating.value = false }
}

const applyProcessGuide = async () => {
  if (!currentDomainId.value || !processGuidePreview.value) return
  processGuideApplying.value = true
  try {
    const preview = processGuidePreview.value
    const processJson = {
      processName: preview.process_name,
      domainId: currentDomainId.value,
      processType: preview.process_type,
      generatedBy: preview.generation_mode,
      nodes: preview.nodes,
      edges: preview.edges,
      version: '1.0',
      createdAt: new Date().toISOString(),
      createdBy: appStore.user?.username
    }
    const res = await processApi.create(currentDomainId.value, {
      process_name: preview.process_name,
      process_desc: preview.process_desc,
      process_json: JSON.stringify(processJson)
    })
    processGuideDialogVisible.value = false
    await loadProcesses()
    if (res.data) openFlowEditor(res.data)
    ElMessage.success('流程图已创建，可继续编辑')
  } catch (e) {} finally { processGuideApplying.value = false }
}

const selectProcess = (_proc: any) => {}

const formatDate = (dateStr: string) => {
  if (!dateStr) return ''
  return new Date(dateStr).toLocaleDateString('zh-CN')
}

// Forms
const entityDialogVisible = ref(false)
const relationDialogVisible = ref(false)
const propertyDialogVisible = ref(false)
const processDialogVisible = ref(false)
const entityDialogMode = ref<'create' | 'edit'>('create')
const relationDialogMode = ref<'create' | 'edit'>('create')
const propertyDialogMode = ref<'create' | 'edit'>('create')
const editingEntityId = ref('')
const editingRelationId = ref('')
const editingRelationTableName = ref('')
const editingPropertyId = ref('')
const entityForm = ref(createEmptyEntityForm())
const relationForm = ref(createEmptyRelationForm())
const propertyForm = ref(createEmptyPropertyForm())
const processForm = ref({ process_name: '', process_desc: '' })

onMounted(() => {
  if (currentDomainId.value) {
    loadCurrentDomainDetail()
    loadActiveSectionData()
  }
  window.addEventListener('resize', syncFlowCanvasSize)
})

watch(() => appStore.currentDomainId, async (domainId) => {
  currentDomainId.value = domainId || ''
  resetDomainState()
  if (currentDomainId.value) {
    await loadCurrentDomainDetail()
    await loadActiveSectionData()
  }
}, { immediate: false })

watch(fixedBuildSection, async (section) => {
  activeTab.value = section || 'graph'
  resetDomainState()
  await loadActiveSectionData()
}, { immediate: false })

watch(() => currentFlow.process_id, async (processId) => {
  if (!processId) return
  await nextTick()
  syncFlowCanvasSize()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', syncFlowCanvasSize)
})
</script>

<style scoped>
.ontology-build-page { height: calc(100vh - 90px); display: flex; flex-direction: column; }
.top-bar { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; flex-shrink: 0; }
.top-bar-end { justify-content: flex-end; }
.tab-section { display: flex; align-items: center; gap: 10px; }
.toolbar { display: flex; gap: 8px; padding: 8px 0; align-items: center; flex-shrink: 0; }
.drag-hint { font-size: 12px; color: #999; margin-left: 8px; }
.graph-container, .flow-container { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.graph-layout { flex: 1; display: flex; gap: 10px; min-height: 0; overflow: hidden; }
.graph-area { flex: 1; min-height: 0; background: #fff; border: 1px solid #e8e8e8; border-radius: 8px; overflow: auto; }
.svg-graph { width: max-content; min-height: 100%; }
.graph-node { cursor: grab; user-select: none; transition: opacity .18s ease; }
.graph-node:active { cursor: grabbing; }
.graph-node.is-selected rect { stroke: #f59e0b; stroke-width: 4; filter: drop-shadow(0 2px 5px rgba(245, 158, 11, .42)); }
.graph-node.is-related rect { stroke: #409EFF; stroke-width: 3; filter: drop-shadow(0 1px 3px rgba(64, 158, 255, .28)); }
.graph-node.is-muted { opacity: .22; }
.graph-edge { cursor: pointer; transition: opacity .18s ease; }
.graph-edge.is-related line { stroke: #409EFF; stroke-width: 3.5; }
.graph-edge.is-related rect { stroke: #409EFF; fill: #ecf5ff; }
.graph-edge.is-related text { fill: #1d5fa7; font-weight: 700; }
.graph-edge.is-muted { opacity: .15; }
.graph-connector { cursor: crosshair; }
.property-panel { width: 320px; background: #fff; border: 1px solid #e8e8e8; border-radius: 8px; padding: 16px; overflow-y: auto; flex-shrink: 0; }
.property-panel h4 { color: #1a3a5c; margin-bottom: 12px; }
.entity-info { font-size: 13px; color: #666; margin-bottom: 16px; }
.entity-info p { margin: 3px 0; }
.property-list h5 { margin: 8px 0; font-size: 14px; }
.property-list-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.prop-item { display: flex; align-items: center; gap: 6px; padding: 5px 0; font-size: 12px; border-bottom: 1px solid #f0f0f0; }
.prop-name { color: #333; font-weight: 500; }
.prop-type { color: #999; font-family: monospace; font-size: 11px; }
.prop-desc { color: #666; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.guide-dialog { display: flex; flex-direction: column; gap: 14px; }
.guide-banner { display: flex; justify-content: space-between; gap: 16px; padding: 14px 16px; background: linear-gradient(135deg, #f7fbff 0%, #eef5ff 100%); border: 1px solid #d6e5ff; border-radius: 12px; }
.guide-banner-main { flex: 1; }
.guide-banner-title { display: flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 600; color: #1a3a5c; }
.guide-banner-empty { color: #999; font-weight: 400; }
.guide-banner-desc { margin: 8px 0 0; font-size: 12px; line-height: 1.7; color: #60748b; }
.guide-banner-side { display: flex; align-items: center; gap: 10px; white-space: nowrap; font-size: 12px; color: #4d647f; }
.guide-steps-shell { display: flex; flex-direction: column; gap: 8px; padding: 12px 14px; background: linear-gradient(135deg, #fffdf7 0%, #fff9ef 100%); border: 1px solid #f0dec3; border-radius: 12px; }
.guide-steps-nav { display: flex; flex-wrap: wrap; gap: 10px; }
.guide-step-chip { display: inline-flex; align-items: center; gap: 8px; padding: 8px 12px; border-radius: 999px; border: 1px solid #d8e3f0; background: #fff; color: #6b7d92; font-size: 12px; cursor: pointer; transition: all .18s ease; }
.guide-step-chip:disabled { cursor: not-allowed; opacity: .55; }
.guide-step-chip.is-unlocked:not(:disabled):hover { border-color: #8bb5e5; color: #2f5f92; transform: translateY(-1px); }
.guide-step-chip.is-active { border-color: #2f6fb0; background: #2f6fb0; color: #fff; box-shadow: 0 6px 14px rgba(47, 111, 176, .18); }
.guide-step-index { display: inline-flex; align-items: center; justify-content: center; width: 20px; height: 20px; border-radius: 999px; background: rgba(47, 111, 176, .1); font-weight: 600; }
.guide-step-chip.is-active .guide-step-index { background: rgba(255,255,255,.22); }
.guide-step-label { font-weight: 600; }
.guide-step-caption { font-size: 12px; color: #7a6b4f; line-height: 1.7; }
.guide-toolbar { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; align-items: center; }
.guide-toolbar-item { width: 100%; }
.guide-step-body { display: flex; flex-direction: column; gap: 14px; }
.guide-layout { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(320px, 0.9fr); gap: 14px; min-height: 360px; }
.guide-document-panel, .guide-table-panel, .guide-preview-panel { background: #fff; border: 1px solid #e6edf5; border-radius: 10px; padding: 14px; }
.guide-panel-title { font-size: 14px; font-weight: 600; color: #1a3a5c; }
.guide-panel-hint { margin-top: 8px; font-size: 12px; color: #7a8ca2; line-height: 1.7; }
.guide-panel-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.guide-panel-actions { display: flex; align-items: center; gap: 8px; }
.guide-panel-meta { font-size: 12px; color: #71839a; }
.guide-document-actions { display: flex; gap: 8px; align-items: center; }
.guide-upload-meta { margin-bottom: 10px; display: flex; flex-wrap: wrap; gap: 10px; font-size: 12px; color: #69809b; }
.guide-control-group { display: flex; flex-direction: column; gap: 8px; margin-top: 10px; }
.guide-control-label { font-size: 12px; font-weight: 600; color: #496684; }
.guide-tag-checkboxes { display: flex; flex-wrap: wrap; gap: 8px 14px; }
.guide-pattern-type-hint { margin: 0 0 8px; font-size: 12px; color: #71839a; }
.guide-pattern-desc { color: #8a99aa; font-size: 12px; }
.guide-mode-option-desc { float: right; margin-left: 12px; color: #8a99aa; font-size: 12px; }
.guide-selected-patterns { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 10px; font-size: 12px; color: #58718b; }
.guide-pattern-empty { margin-top: 10px; font-size: 12px; color: #8a99aa; }
.guide-table-search { margin-bottom: 10px; }
.guide-table-list { height: 100%; min-height: 280px; max-height: 420px; overflow-y: auto; }
.guide-checkbox-group { display: flex; flex-direction: column; gap: 8px; width: 100%; }
.guide-table-item { display: flex; flex-direction: column; gap: 4px; padding: 10px 12px; border: 1px solid #edf2f7; border-radius: 8px; background: #fafcff; }
.guide-table-name { font-weight: 600; color: #24384f; }
.guide-table-owner { font-size: 11px; color: #7b8da5; }
.guide-table-comment { font-size: 12px; color: #5d7088; line-height: 1.5; }
.guide-preview { display: flex; flex-direction: column; gap: 10px; }
.guide-preview-head { display: flex; justify-content: space-between; gap: 10px; align-items: center; }
.guide-preview-tags, .guide-apply-summary { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; font-size: 12px; color: #61748b; }
.guide-preview-summary { font-size: 12px; line-height: 1.7; color: #54687f; }
.guide-preview-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.guide-table-chip-list { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }

.natural-adjust-dialog { display: flex; flex-direction: column; gap: 14px; }
.natural-adjust-banner { display: flex; justify-content: space-between; gap: 16px; padding: 14px 16px; background: linear-gradient(135deg, #fff8ee 0%, #fff2dc 100%); border: 1px solid #f6d8a7; border-radius: 12px; }
.natural-adjust-banner-main { flex: 1; }
.natural-adjust-banner-title { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 14px; font-weight: 600; color: #6b4b16; }
.natural-adjust-banner-desc { margin: 8px 0 0; font-size: 12px; line-height: 1.7; color: #8a6631; }
.natural-adjust-banner-side { display: flex; align-items: center; gap: 10px; white-space: nowrap; font-size: 12px; color: #7b5b27; }
.natural-adjust-form { display: flex; flex-direction: column; gap: 10px; }
.natural-adjust-model { width: 100%; }
.natural-adjust-hint { font-size: 12px; color: #7a8ca2; line-height: 1.7; }
.natural-adjust-preview { display: flex; flex-direction: column; gap: 10px; }
.natural-adjust-summary { padding: 10px 12px; background: #fffaf1; border: 1px solid #f2e3bf; border-radius: 8px; font-size: 13px; color: #6b5330; line-height: 1.7; }
.natural-adjust-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.natural-adjust-warnings { display: flex; flex-direction: column; gap: 8px; font-size: 12px; color: #8a5a1f; line-height: 1.6; }

/* Flow List */
.flow-overview-card { display: flex; justify-content: space-between; gap: 16px; padding: 16px 18px; margin-bottom: 10px; background: linear-gradient(135deg, #f6fbff 0%, #eef6ff 100%); border: 1px solid #d7e8ff; border-radius: 12px; flex-shrink: 0; }
.flow-overview-main { flex: 1; }
.flow-overview-title { font-size: 18px; font-weight: 600; color: #1a3a5c; margin-bottom: 8px; }
.flow-overview-desc { margin: 0; font-size: 13px; line-height: 1.7; color: #4f6480; }
.flow-overview-steps { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
.flow-step { padding: 4px 10px; background: #fff; border: 1px solid #cfe0ff; border-radius: 999px; font-size: 12px; color: #27507c; }
.flow-overview-side { width: 320px; padding-left: 16px; border-left: 1px dashed #c8d9ef; }
.flow-side-title { font-size: 13px; font-weight: 600; color: #40658d; margin-bottom: 10px; }
.flow-side-current { font-size: 16px; font-weight: 600; color: #1a3a5c; margin-bottom: 8px; }
.flow-side-stats { display: flex; flex-wrap: wrap; gap: 10px; font-size: 12px; color: #5d7390; margin-bottom: 8px; }
.flow-side-desc { margin: 0; font-size: 12px; line-height: 1.6; color: #6c8098; }
.flow-node-legend { display: flex; flex-wrap: wrap; gap: 8px; }
.flow-legend-chip { padding: 4px 10px; border: 1px solid; border-radius: 999px; background: rgba(255,255,255,0.85); font-size: 12px; }
.flow-list-panel { flex: 1; overflow-y: auto; }
.flow-cards { display: flex; gap: 12px; flex-wrap: wrap; padding: 4px 0; }
.flow-card { width: 300px; cursor: pointer; transition: all .2s; }
.flow-card:hover { transform: translateY(-2px); box-shadow: 0 4px 16px rgba(0,0,0,0.12); }
.flow-card-header { display: flex; justify-content: space-between; align-items: center; }
.flow-card-header h4 { margin: 0; font-size: 15px; color: #1a3a5c; }
.flow-desc { font-size: 13px; color: #666; margin: 8px 0; }
.flow-meta { display: flex; gap: 12px; font-size: 11px; color: #aaa; margin-top: 6px; }
.flow-actions { margin-top: 6px; text-align: right; }

/* Flow Editor */
.flow-breadcrumb { display: flex; align-items: center; gap: 8px; margin-left: 12px; font-size: 13px; }
.flow-editor { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.flow-editor-summary { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; padding: 12px 14px; margin-bottom: 8px; background: #fff; border: 1px solid #e8eef5; border-radius: 8px; }
.flow-editor-summary h4 { margin: 0 0 6px; font-size: 15px; color: #1a3a5c; }
.flow-editor-summary p { margin: 0; font-size: 12px; color: #66788a; line-height: 1.6; }
.flow-editor-hint { margin-top: 8px; font-size: 12px; color: #409eff; line-height: 1.7; }
.flow-editor-summary-stats { display: flex; gap: 10px; font-size: 12px; color: #56708f; white-space: nowrap; }
.flow-editor-body { flex: 1; display: flex; gap: 10px; min-height: 0; }
.flow-editor-toolbar { display: flex; justify-content: space-between; align-items: center; padding: 6px 0; flex-shrink: 0; }
.node-palette { display: flex; align-items: center; gap: 6px; }
.palette-label { font-size: 12px; color: #666; margin-right: 4px; }
.palette-node { padding: 4px 10px; border-radius: 4px; border: 2px solid; font-size: 12px; cursor: grab; color: #333; user-select: none; transition: transform .15s; }
.palette-node:hover { transform: scale(1.05); }
.palette-node:active { cursor: grabbing; }
.flow-editor-actions { display: flex; gap: 6px; }
.flow-selection-state { display: inline-flex; align-items: center; padding: 0 8px; font-size: 12px; color: #5f6b7a; background: #eef5ff; border: 1px solid #cfe0ff; border-radius: 999px; }
.flow-canvas { flex: 1; background: #fff; border: 1px solid #e8e8e8; border-radius: 8px; overflow: hidden; min-height: 400px; }
.flow-node-group { cursor: grab; }
.flow-node-group:active { cursor: grabbing; }
.flow-handle, .flow-handle-label { cursor: crosshair; }
.flow-side-panel { width: 320px; flex-shrink: 0; display: flex; flex-direction: column; min-height: 0; }

@media (max-width: 1100px) {
  .guide-toolbar,
  .guide-layout,
  .guide-preview-grid,
  .natural-adjust-grid {
    grid-template-columns: 1fr;
  }
}
.flow-config-panel { background: #fff; border: 1px solid #e8e8e8; border-radius: 8px; padding: 12px; height: 100%; overflow-y: auto; flex-shrink: 0; }
.flow-config-panel h4 { font-size: 14px; color: #1a3a5c; margin-bottom: 8px; }
.flow-config-empty { background: #fff; border: 1px dashed #cbd9e8; border-radius: 8px; padding: 16px; color: #64758b; font-size: 13px; line-height: 1.7; }
.flow-config-empty h4 { margin: 0 0 10px; font-size: 14px; color: #1a3a5c; }
.flow-config-empty p { margin: 0 0 10px; }
</style>
