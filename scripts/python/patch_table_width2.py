with open("source_code/0.2_RAG系统/project-rag-v1.1/app/templates/project.html", "r") as f:
    content = f.read()

old_block = """          <td style="max-width: 320px; white-space: normal; word-break: break-all;">
            <div style="font-weight: 600; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;" title="{{ d.filename }}">
              <a href="/documents/{{ d.id }}">{{ d.filename }}</a>"""

new_block = """          <td style="width: 380px; max-width: 380px; white-space: normal; word-break: break-all;">
            <div style="font-weight: 600; line-height: 1.4; margin-bottom: 4px;">
              <a href="/documents/{{ d.id }}" style="display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; text-overflow: ellipsis;" title="{{ d.filename }}">{{ d.filename }}</a>
            </div>
            <div style="display: flex; align-items: center; gap: 6px;">"""

content = content.replace(old_block, new_block)

# Since we split the badges into a new flex div, we need to close it.
# Let's see the rest of the block:
#              {% if d.duplicate_of_id %}
#              <span class="badge badge-rose" style="margin-left: 6px;" title="检测到内容指纹完全一致的重复文件">重复件</span>
#              {% endif %}
#              {% if d.invoice_no %}
#              <span class="badge badge-emerald" style="margin-left: 4px;" title="发票号码: {{ d.invoice_no }}">含发票</span>
#              {% endif %}
#            </div>

old_badges = """              {% if d.duplicate_of_id %}
              <span class="badge badge-rose" style="margin-left: 6px;" title="检测到内容指纹完全一致的重复文件">重复件</span>
              {% endif %}
              {% if d.invoice_no %}
              <span class="badge badge-emerald" style="margin-left: 4px;" title="发票号码: {{ d.invoice_no }}">含发票</span>
              {% endif %}
            </div>"""

new_badges = """              {% if d.duplicate_of_id %}
              <span class="badge badge-rose" title="检测到内容指纹完全一致的重复文件">重复件</span>
              {% endif %}
              {% if d.invoice_no %}
              <span class="badge badge-emerald" title="发票号码: {{ d.invoice_no }}">含发票</span>
              {% endif %}
            </div>"""

content = content.replace(old_badges, new_badges)

with open("source_code/0.2_RAG系统/project-rag-v1.1/app/templates/project.html", "w") as f:
    f.write(content)
