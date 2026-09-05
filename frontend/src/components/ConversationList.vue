<template>
  <aside class="sidebar">
    <div class="sidebar-head">
      <span class="sidebar-title">会话</span>
      <button class="icon-btn" title="新建会话" @click="onNew">
        <Icon name="plus" :size="17" />
      </button>
    </div>

    <div v-if="groups.length" class="conv-groups">
      <div v-for="group in groups" :key="group.label" class="conv-group">
        <div class="group-label">{{ group.label }}</div>
        <div
          v-for="c in group.items"
          :key="c.id"
          class="conv-item"
          :class="{ active: c.id === chat.currentId }"
          @click="onSelect(c.id)"
        >
          <div class="conv-main">
            <span class="conv-title">{{ c.title || '新对话' }}</span>
            <span class="icon-btn del-conv" title="删除会话" @click.stop="askDelete(c)">
              <Icon name="trash" :size="13" />
            </span>
          </div>
          <div class="conv-meta">{{ metaText(c) }}</div>
        </div>
      </div>
    </div>
    <div v-else class="conv-empty">暂无会话，点击右上角新建</div>

    <div class="sidebar-foot">Dev 模式 · 会话全局可见</div>

    <Modal v-if="deleting" title="删除会话" size="sm" @close="deleting = null">
      <p style="margin: 0; line-height: 1.7">
        确定删除「{{ deleting.title || '新对话' }}」吗？删除后不可恢复。
      </p>
      <template #footer>
        <button class="btn btn-ghost" @click="deleting = null">取消</button>
        <button class="btn btn-danger" @click="onDeleteConfirmed">删除</button>
      </template>
    </Modal>
  </aside>
</template>

<script setup>
import { computed, ref } from 'vue'
import Icon from './Icon.vue'
import Modal from './Modal.vue'
import { chat } from '../stores/chat'
import { formatWhen, shortModel } from '../display'

const deleting = ref(null)

const groups = computed(() => {
  const labels = ['今天', '昨天', '近 7 天', '更早']
  const buckets = labels.map((label) => ({ label, items: [] }))
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const day = (t) => Math.floor((startOfToday - new Date(t)) / 86400000)
  for (const c of chat.conversations) {
    const diff = day(c.updated_at || Date.now())
    const idx = diff <= 0 ? 0 : diff === 1 ? 1 : diff <= 7 ? 2 : 3
    buckets[idx].items.push(c)
  }
  return buckets.filter((b) => b.items.length)
})

function metaText(c) {
  const parts = []
  const model = shortModel(c.model)
  if (model) parts.push(model)
  const when = formatWhen(c.updated_at)
  if (when) parts.push(when)
  return parts.join(' · ')
}

async function onSelect(id) {
  try {
    await chat.selectConversation(id)
  } catch (err) {
    chat.error = err.message
  }
}

async function onNew() {
  try {
    await chat.createConversation()
  } catch (err) {
    chat.error = err.message
  }
}

function askDelete(c) {
  deleting.value = c
}

async function onDeleteConfirmed() {
  const target = deleting.value
  deleting.value = null
  if (!target) return
  try {
    await chat.deleteConversation(target.id)
  } catch (err) {
    chat.error = err.message
  }
}
</script>
