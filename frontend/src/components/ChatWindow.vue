<template>
  <main v-if="chat.currentId" class="chat">
    <header class="chat-head">
      <div class="chat-title-block">
        <div class="chat-title">{{ currentTitle }}</div>
        <div v-if="chat.currentModel" class="chat-subtitle">
          {{ shortModel(chat.currentModel) }} · {{ formatWhen(currentConv?.updated_at) }}
        </div>
      </div>
      <span v-if="chat.streaming" class="live-badge"><span class="dot"></span>分析中</span>
    </header>

    <div v-if="chat.error" class="error-banner">
      <span>{{ chat.error }}</span>
      <button class="icon-btn" title="关闭" @click="chat.error = ''">
        <Icon name="x" :size="14" />
      </button>
    </div>

    <div ref="messagesEl" class="messages">
      <div class="msg-list">
        <div v-if="!chat.messages.length" class="welcome">
          <div class="welcome-icon"><Icon name="search" :size="24" /></div>
          <div class="welcome-title">有什么可以帮你？</div>
          <div class="welcome-sub">输入自然语言，进行土地变化查询、统计分析与快报生成</div>
          <div class="suggestions">
            <button
              v-for="s in suggestions"
              :key="s"
              class="suggest-chip"
              :disabled="chat.streaming"
              @click="sendSuggestion(s)"
            >
              {{ s }}
            </button>
          </div>
        </div>
        <MessageBubble
          v-for="(m, i) in chat.messages"
          :key="m.id"
          :message="m"
          :can-edit="i === lastUserIndex && !chat.streaming"
        />
      </div>
    </div>

    <footer class="composer-area">
      <div class="composer">
        <textarea
          ref="ta"
          v-model="draft"
          rows="1"
          placeholder="输入消息，Enter 发送，Shift+Enter 换行"
          :disabled="chat.streaming"
          @keydown.enter.exact.prevent="onSend"
          @input="autosize"
        />
        <div class="composer-bar">
          <div class="composer-left">
            <button class="icon-btn" title="添加附件" :disabled="chat.streaming" @click="onAttach">
              <Icon name="paperclip" :size="17" />
            </button>

            <div ref="pickerEl" class="model-picker">
              <button
                class="model-btn"
                :title="'当前模型：' + (chat.currentModel || '未选择')"
                :disabled="chat.streaming"
                @click="pickerOpen = !pickerOpen"
              >
                <span class="model-dot"></span>
                <span class="model-text">{{ shortModel(chat.currentModel) || '选择模型' }}</span>
                <Icon name="chevron-down" :size="14" />
              </button>
              <div v-if="pickerOpen" class="model-menu">
                <div
                  v-for="m in chat.models"
                  :key="m.id"
                  class="model-opt"
                  :class="{ active: m.id === chat.currentModel, disabled: !m.available }"
                  @click="onPickModel(m)"
                >
                  <span class="model-name">{{ m.id }}</span>
                  <span v-if="m.id === chat.currentModel" class="model-note">使用中</span>
                  <span v-else-if="!m.available" class="model-note">未配置</span>
                </div>
              </div>
            </div>

            <span v-if="tip" class="composer-tip">{{ tip }}</span>
          </div>

          <button
            class="send-btn"
            title="发送"
            :disabled="chat.streaming || !draft.trim()"
            @click="onSend"
          >
            <Icon v-if="!chat.streaming" name="send" :size="17" />
            <span v-else class="spinner"></span>
          </button>
        </div>
      </div>
    </footer>
  </main>

  <main v-else class="chat chat-empty">从左侧选择或新建一个会话</main>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import Icon from './Icon.vue'
import MessageBubble from './MessageBubble.vue'
import { chat } from '../stores/chat'
import { formatWhen, shortModel } from '../display'

const draft = ref('')
const tip = ref('')
const messagesEl = ref(null)
const ta = ref(null)
const pickerEl = ref(null)
const pickerOpen = ref(false)
let tipTimer = null

const suggestions = [
  '耕地转为建设用地的面积是多少？',
  '按三大类统计各类土地变化面积',
  '生成本期土地变化监测快报',
  '分析养老机构的步行可达性',
]

const currentConv = computed(
  () => chat.conversations.find((c) => c.id === chat.currentId) || null,
)
const currentTitle = computed(() => currentConv.value?.title || '新对话')

// 只有最后一条用户问题允许“修改并重新生成”
const lastUserIndex = computed(() => {
  for (let i = chat.messages.length - 1; i >= 0; i -= 1) {
    if (chat.messages[i].role === 'user') return i
  }
  return -1
})

// 消息变化时（流式 token 逐字追加）自动滚动到底部
watch(
  () => [chat.messages.length, chat.messages[chat.messages.length - 1]?.content],
  async () => {
    await nextTick()
    if (messagesEl.value) messagesEl.value.scrollTop = messagesEl.value.scrollHeight
  },
)

// 模型菜单打开时，点击菜单外部自动收起
watch(pickerOpen, (open) => {
  if (open) {
    document.addEventListener('mousedown', onOutside)
    document.addEventListener('keydown', onKeydown)
  } else {
    document.removeEventListener('mousedown', onOutside)
    document.removeEventListener('keydown', onKeydown)
  }
})

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onOutside)
  document.removeEventListener('keydown', onKeydown)
  if (tipTimer) clearTimeout(tipTimer)
})

function onOutside(e) {
  if (pickerEl.value && !pickerEl.value.contains(e.target)) pickerOpen.value = false
}

function onKeydown(e) {
  if (e.key === 'Escape') pickerOpen.value = false
}

function autosize() {
  if (!ta.value) return
  ta.value.style.height = 'auto'
  ta.value.style.height = Math.min(ta.value.scrollHeight, 160) + 'px'
}

function sendSuggestion(text) {
  if (chat.sendMessage(text)) {
    pickerOpen.value = false
    nextTick(autosize)
  }
}

function onSend() {
  const text = draft.value
  if (!text.trim() || !chat.sendMessage(text)) return
  draft.value = ''
  if (ta.value) ta.value.style.height = 'auto'
}

async function onPickModel(m) {
  pickerOpen.value = false
  if (!m.available || m.id === chat.currentModel) return
  try {
    await chat.switchModel(m.id)
  } catch (err) {
    chat.error = err.message
  }
}

function onAttach() {
  tip.value = '附件上传功能暂未开放'
  if (tipTimer) clearTimeout(tipTimer)
  tipTimer = setTimeout(() => {
    tip.value = ''
  }, 2500)
}
</script>
