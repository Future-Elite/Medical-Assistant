<script setup>
import { computed } from 'vue'
const props = defineProps({ modelValue: String, models: { type:Array, default:()=>[] }, disabled:Boolean })
const emit = defineEmits(['update:modelValue'])
const policy = computed(() => props.models.find(m=>m.id===props.modelValue)?.thinking_policy)
</script>
<template>
  <div class="model-select">
    <label class="field">生成模型<select :value="modelValue" :disabled="disabled" @change="emit('update:modelValue', $event.target.value)"><option v-for="model in models" :key="model.id" :value="model.id">{{ model.name }}</option></select></label>
    <p v-if="policy" class="model-policy"><strong>{{ policy.label }}</strong> · {{ policy.note }}</p>
  </div>
</template>
