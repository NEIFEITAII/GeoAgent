<template>
  <Teleport to="body">
    <div class="modal-mask" @mousedown.self="close">
      <div class="modal" :class="sizeClass" :style="width ? { width } : {}" role="dialog" aria-modal="true">
        <div class="modal-head">
          <span class="modal-title">{{ title }}</span>
          <button class="icon-btn modal-close" title="关闭" @click="close">
            <Icon name="x" :size="16" />
          </button>
        </div>
        <div class="modal-body">
          <slot />
        </div>
        <div v-if="$slots.footer" class="modal-foot">
          <slot name="footer" />
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted } from 'vue'
import Icon from './Icon.vue'

const props = defineProps({
  title: { type: String, default: '' },
  // 预设尺寸：sm（小确认框） / lg（大预览框）；传 width 时优先使用自定义宽度
  size: { type: String, default: '' },
  width: { type: String, default: '' },
})

const emit = defineEmits(['close'])

const sizeClass = computed(() => (props.size ? `modal-${props.size}` : ''))

function close() {
  emit('close')
}

function onKeydown(e) {
  if (e.key === 'Escape') close()
}

onMounted(() => {
  document.addEventListener('keydown', onKeydown)
  document.body.style.overflow = 'hidden'
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
  document.body.style.overflow = ''
})
</script>
