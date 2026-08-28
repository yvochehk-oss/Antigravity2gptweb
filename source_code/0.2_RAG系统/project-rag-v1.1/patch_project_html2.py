import sys

with open("app/templates/project.html", "r") as f:
    content = f.read()

# The first injection is before the FIRST {% endblock %}
# We can find the block by string matching and removing it.
bad_block = """<!-- 目录选择器弹窗 -->
<div id="modal-dir-picker" class="modal-overlay" style="z-index: 9999;">
  <div class="glass-panel-elevated modal-content" style="width: 600px; max-height: 80vh; display: flex; flex-direction: column;">
    <div class="modal-header">
      <h3 style="color: #fff; font-size: 16px;">📂 选择文件夹</h3>
      <button class="modal-close" onclick="document.getElementById('modal-dir-picker').classList.remove('active')">&times;</button>
    </div>
    <div style="margin-bottom: 12px; display: flex; gap: 8px;">
      <input type="text" id="dir-picker-path" class="form-input" style="flex: 1; font-family: monospace; font-size: 12px;" readonly>
      <button type="button" class="btn btn-secondary" onclick="goUpDir()">⬆️ 返回上级</button>
      <button type="button" class="btn btn-secondary" onclick="loadDir('/Volumes')">🌐 局域网/外挂盘</button>
    </div>
    <div id="dir-picker-list" style="flex: 1; overflow-y: auto; background: rgba(0,0,0,0.3); border: 1px solid var(--border-color); border-radius: 6px; padding: 6px; min-height: 300px;">
      <!-- Dir list items go here -->
    </div>
    <div style="display: flex; justify-content: flex-end; gap: 10px; margin-top: 14px;">
      <button type="button" class="btn btn-secondary" onclick="document.getElementById('modal-dir-picker').classList.remove('active')">取消</button>
      <button type="button" class="btn btn-primary" onclick="confirmDirSelection()">选中当前文件夹</button>
    </div>
  </div>
</div>

<script>
let currentPickerPath = '';

async function loadDir(path = '') {
  const listEl = document.getElementById('dir-picker-list');
  listEl.innerHTML = '<div style="padding: 10px; color: var(--text-muted); text-align: center;">加载中...</div>';
  
  try {
    const res = await fetch('/api/v1/fs/list-dirs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: path })
    });
    const data = await res.json();
    
    if (data.error) {
      listEl.innerHTML = `<div style="padding: 10px; color: #ef4444; text-align: center;">${data.error}</div>`;
      return;
    }
    
    currentPickerPath = data.current;
    document.getElementById('dir-picker-path').value = data.current;
    
    listEl.innerHTML = '';
    if (data.dirs.length === 0) {
      listEl.innerHTML = '<div style="padding: 10px; color: var(--text-muted); text-align: center;">(空文件夹)</div>';
    } else {
      data.dirs.forEach(d => {
        const item = document.createElement('div');
        item.style.cssText = 'padding: 8px 10px; cursor: pointer; border-radius: 4px; display: flex; align-items: center; gap: 8px; color: var(--text-primary); transition: background 0.2s;';
        item.onmouseover = () => item.style.background = 'rgba(76, 215, 246, 0.1)';
        item.onmouseout = () => item.style.background = 'transparent';
        item.innerHTML = `<span>📁</span> <span>${d.name}</span>`;
        item.onclick = () => loadDir(d.path);
        listEl.appendChild(item);
      });
    }
  } catch (err) {
    listEl.innerHTML = `<div style="padding: 10px; color: #ef4444; text-align: center;">网络错误</div>`;
  }
}

function openDirPicker() {
  document.getElementById('modal-dir-picker').classList.add('active');
  const initialPath = document.getElementById('import-path-input').value || '/Users/yvoche';
  loadDir(initialPath);
}

function goUpDir() {
  if (currentPickerPath && currentPickerPath !== '/') {
    const parent = currentPickerPath.substring(0, currentPickerPath.lastIndexOf('/')) || '/';
    loadDir(parent);
  }
}

function confirmDirSelection() {
  if (currentPickerPath) {
    document.getElementById('import-path-input').value = currentPickerPath;
    document.getElementById('modal-dir-picker').classList.remove('active');
  }
}
</script>
"""

content = content.replace(bad_block, "", 1) # only replace the first occurrence

with open("app/templates/project.html", "w") as f:
    f.write(content)

print("Fixed project.html")
