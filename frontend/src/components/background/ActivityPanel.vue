<script setup lang="ts">
import { computed } from 'vue'
import { dotClass, heldLine, playPauseButton } from '../../types/background'
import type { BackgroundProcess, ProcessAction } from '../../types/background'

const props = defineProps<{ process: BackgroundProcess; all: BackgroundProcess[] }>()
const emit = defineEmits<{ command: [action: ProcessAction] }>()

const p = computed(() => props.process)
const pp = computed(() => playPauseButton(p.value))
const stopDisabled = computed(() => !p.value.capabilities.can_stop)
const pct = computed(() => {
  const g = p.value.progress
  return g && g.total > 0 ? Math.min(100, Math.round((g.done / g.total) * 100)) : 0
})
const idle = computed(() => p.value.state === 'idle' || p.value.state === 'stopped')
const held = computed(() => heldLine(p.value, props.all))
const sub = computed(() => p.value.error || p.value.activity || p.value.state)
const ppLabel = computed(() => `${pp.value.kind === 'pause' ? 'Pause' : pp.value.action === 'resume' ? 'Resume' : 'Start'} ${p.value.label}`)

function stop() {
  if (p.value.capabilities.confirm_stop && !window.confirm(`Stop ${p.value.label}?`)) return
  emit('command', 'stop')
}
</script>

<template>
  <div class="card" :class="{ idle }" :data-state="p.state" data-testid="activity-panel">
    <div class="top">
      <span class="dot" :class="dotClass(p.state)" aria-hidden="true" />
      <span class="name" :title="p.label">{{ p.label }}</span>
      <button
        type="button"
        class="btn"
        :disabled="pp.disabled"
        :aria-label="ppLabel"
        :title="ppLabel"
        data-testid="activity-playpause"
        @click="emit('command', pp.action)"
      >
        <i :class="pp.kind === 'pause' ? 'pi pi-pause' : 'pi pi-play'" />
      </button>
      <button
        type="button"
        class="btn"
        :disabled="stopDisabled"
        :aria-label="`Stop ${p.label}`"
        :title="`Stop ${p.label}`"
        data-testid="activity-stop"
        @click="stop"
      >
        <i class="pi pi-stop" />
      </button>
    </div>
    <div class="track" role="progressbar" :aria-valuenow="pct" aria-valuemin="0" aria-valuemax="100" :aria-label="`${p.label} progress`">
      <div v-if="p.progress" class="fill" :class="{ err: p.state === 'failed' }" :style="{ width: pct + '%' }" />
    </div>
    <div class="sub" :class="{ errtext: p.state === 'failed' }" :title="p.error || p.activity">{{ sub }}</div>
    <div v-if="held" class="sub heldline">{{ held }}</div>
  </div>
</template>

<style scoped>
.card {
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  padding: 8px 9px;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.card.idle .name,
.card.idle .sub,
.card.idle .dot { opacity: 0.62; }
.top { display: flex; align-items: center; gap: 5px; }
.name {
  font-weight: 600;
  font-size: 13px;
  color: var(--text-color);
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--text-color-secondary); flex: none; }
.dot.run { background: #22c55e; }
.dot.wait { background: var(--primary-color); }
.dot.paused { background: var(--warn); }
.dot.err { background: var(--danger-color); }
.btn {
  width: 22px;
  height: 22px;
  flex: none;
  border: none;
  border-radius: 4px;
  background: var(--surface-hover);
  color: var(--text-color);
  font-size: 10px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}
.btn:hover:not(:disabled) { filter: brightness(1.15); }
.btn:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 1px; }
.btn:disabled { opacity: 0.35; cursor: not-allowed; }
.track { height: 4px; background: var(--surface-border); border-radius: 2px; overflow: hidden; }
.fill { height: 100%; background: var(--primary-color); }
.fill.err { background: var(--danger-color); }
.sub {
  color: var(--text-color-secondary);
  font-size: 11.5px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.errtext { color: var(--danger-color); }
.heldline { color: var(--warn); }
</style>
