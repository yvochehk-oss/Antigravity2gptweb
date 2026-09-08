import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import CopilotView from '../../src/modules/ai/CopilotView.vue'
import { useCopilotStore } from '../../src/stores/copilot.store'

describe('CopilotView Bottom Dock Layout', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('renders pinned bottom dock containing the query form', () => {
    const wrapper = mount(CopilotView, {
      global: {
        stubs: {
          MentionChip: true
        }
      }
    })

    const dock = wrapper.find('footer.copilot-bottom-dock')
    expect(dock.exists()).toBe(true)

    const form = dock.find('form')
    expect(form.exists()).toBe(true)

    const input = dock.find('input[type="text"]')
    expect(input.exists()).toBe(true)
  })

  it('renders active project focus bar inside the pinned bottom dock when a project is active', async () => {
    const copilot = useCopilotStore()
    copilot.activeProject = {
      id: 'p-001',
      name: '天府国际金融中心二期'
    }

    const wrapper = mount(CopilotView, {
      global: {
        stubs: {
          MentionChip: true
        }
      }
    })

    const dock = wrapper.find('footer.copilot-bottom-dock')
    expect(dock.exists()).toBe(true)
    expect(dock.text()).toContain('天府国际金融中心二期')
    expect(dock.text()).toContain('切回集团概览 ✕')
  })
})
