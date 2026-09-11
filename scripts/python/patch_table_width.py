with open("source_code/0.2_RAG系统/project-rag-v1.1/app/templates/project.html", "r") as f:
    content = f.read()

old_block = """          <td>
            <div style="font-weight: 600;">
              <a href="/documents/{{ d.id }}">{{ d.filename }}</a>"""

new_block = """          <td style="max-width: 320px; white-space: normal; word-break: break-all;">
            <div style="font-weight: 600; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;" title="{{ d.filename }}">
              <a href="/documents/{{ d.id }}">{{ d.filename }}</a>"""

content = content.replace(old_block, new_block)
with open("source_code/0.2_RAG系统/project-rag-v1.1/app/templates/project.html", "w") as f:
    f.write(content)
