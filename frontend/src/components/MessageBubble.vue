<template>
  <div class="msg" :class="message.role">
    <div v-if="message.role === 'user'" class="bubble user-bubble">{{ message.content }}</div>

    <template v-else-if="message.role === 'assistant'">
      <div v-if="message.route" class="msg-route">
        → {{ routeLabel(message.route) }}
      </div>
      <div
        v-for="s in message.subagents || []"
        :key="s.id"
        class="subagent-block"
        :class="'subagent-' + (s.status || 'running')"
      >
        <div class="subagent-head">
          {{
            s.status === 'error'
              ? '✕ 子任务失败'
              : s.status === 'done'
                ? '✔ 子任务完成'
                : '▶ 子任务运行中'
          }}
        </div>
        <div class="subagent-prompt">{{ s.prompt }}</div>
        <div v-if="s.content" class="subagent-result">{{ s.content }}</div>
      </div>
      <div class="bubble assistant-bubble">
        <div class="msg-content">
          {{ message.content }}<span v-if="message.streaming" class="cursor" />
        </div>
      </div>
      <ToolCallCard v-for="c in message.toolCalls" :key="c.id" :call="c" />
    </template>

    <div v-else-if="message.role === 'tool-standalone'" class="tool-standalone">
      <div class="tool-result-text">{{ message.content }}</div>
      <ArtifactView v-for="(a, i) in message.artifacts" :key="i" :artifact="a" />
    </div>
  </div>
</template>

<script setup>
import ToolCallCard from './ToolCallCard.vue'
import ArtifactView from './ArtifactView.vue'

defineProps({
  message: { type: Object, required: true },
})

const ROUTE_LABELS = {
  sql: '土地变化数据查询智能体',
  elder_care: '养老可达性分析智能体',
  geo: '地理分析智能体',
  chat: '通用对话智能体',
}

function routeLabel(route) {
  return ROUTE_LABELS[route] || route || '通用对话智能体'
}
</script>
