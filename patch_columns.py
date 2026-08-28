with open("source_code/0.2_RAG系统/project-rag-v1.1/app/templates/project.html", "r") as f:
    content = f.read()

# 1. Increase width of filename column from 380px to 450px
content = content.replace("width: 380px; max-width: 380px;", "width: 450px; max-width: 450px;")

# 2. Add fixed width to entity column and enable double-line wrapping on the badges
old_entity_td = """          <td>
            <div style="display: flex; flex-direction: column; gap: 4px;">
              {% if d.entity_code %}
              {% set document_entity = entity_by_code.get(d.entity_code) %}
              <div>
                {% if document_entity %}
                <span class="badge {{ document_entity.business_role_badge }}" title="经办/归属主体 ({{ document_entity.source }})">
                  {{ document_entity.entity_code }} · {{ document_entity.short_name or document_entity.name }}
                </span>
                {% else %}
                <span class="badge badge-muted">待核实主体 ({{ d.entity_code }})</span>
                {% endif %}
              </div>
              {% endif %}

              {% if d.counterparty_code %}
              {% set cp_entity = entity_by_code.get(d.counterparty_code) %}
              <div style="font-size: 11px; display: flex; align-items: center; gap: 4px;">
                <span style="color: var(--text-muted); font-size: 11px;">往来:</span>
                {% if cp_entity %}
                <span class="badge {{ cp_entity.business_role_badge }}" style="font-size: 11px; padding: 2px 6px;" title="往来交易对手: {{ cp_entity.name }} ({{ cp_entity.source }})">
                  {{ cp_entity.short_name or cp_entity.name }}
                  {% if cp_entity.is_external %}<span style="opacity: 0.8; font-size: 10px; margin-left: 2px;">(系统外)</span>{% endif %}
                </span>
                {% else %}
                <span style="color: var(--text-muted);">{{ d.counterparty_code }}</span>
                {% endif %}
              </div>
              {% endif %}
            </div>
          </td>"""

new_entity_td = """          <td style="width: 240px; max-width: 240px;">
            <div style="display: flex; flex-direction: column; gap: 6px;">
              {% if d.entity_code %}
              {% set document_entity = entity_by_code.get(d.entity_code) %}
              <div>
                {% if document_entity %}
                <span class="badge {{ document_entity.business_role_badge }}" style="white-space: normal; word-break: break-all; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; line-height: 1.3;" title="经办/归属主体 ({{ document_entity.source }})">
                  {{ document_entity.entity_code }} · {{ document_entity.short_name or document_entity.name }}
                </span>
                {% else %}
                <span class="badge badge-muted">待核实主体 ({{ d.entity_code }})</span>
                {% endif %}
              </div>
              {% endif %}

              {% if d.counterparty_code %}
              {% set cp_entity = entity_by_code.get(d.counterparty_code) %}
              <div style="font-size: 11px; display: flex; align-items: flex-start; gap: 4px;">
                <span style="color: var(--text-muted); font-size: 11px; margin-top: 2px; white-space: nowrap;">往来:</span>
                {% if cp_entity %}
                <span class="badge {{ cp_entity.business_role_badge }}" style="font-size: 11px; padding: 2px 6px; white-space: normal; word-break: break-all; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; line-height: 1.3;" title="往来交易对手: {{ cp_entity.name }} ({{ cp_entity.source }})">
                  {{ cp_entity.short_name or cp_entity.name }}
                  {% if cp_entity.is_external %}<span style="opacity: 0.8; font-size: 10px; margin-left: 2px;">(系统外)</span>{% endif %}
                </span>
                {% else %}
                <span style="color: var(--text-muted); margin-top: 2px;">{{ d.counterparty_code }}</span>
                {% endif %}
              </div>
              {% endif %}
            </div>
          </td>"""

content = content.replace(old_entity_td, new_entity_td)

with open("source_code/0.2_RAG系统/project-rag-v1.1/app/templates/project.html", "w") as f:
    f.write(content)
