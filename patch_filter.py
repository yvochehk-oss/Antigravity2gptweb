import re

with open("source_code/0.2_RAG系统/project-rag-v1.1/app/templates/project.html", "r") as f:
    content = f.read()

# 1. Remove Table Headers (<th>)
th_to_remove = """          <th onclick="sortProjectTable(3, 'text')" style="cursor: pointer;" title="点击按业务分类排序">
            业务分类 <span class="sort-icon-p" id="p-sort-3">↕</span>
          </th>
          <th onclick="sortProjectTable(4, 'text')" style="cursor: pointer;" title="点击按业务期间排序">
            业务期间 <span class="sort-icon-p" id="p-sort-4">↕</span>
          </th>"""
content = content.replace(th_to_remove, "")

# 2. Remove Table Data (<td>)
td_to_remove = """          <td>
            <span class="badge badge-muted">{{ d.business_category | category_name if d.business_category else '-' }}</span>
          </td>
          <td class="font-mono" style="font-size: 12px;">
            {{ d.period or '-' }}
          </td>"""
content = content.replace(td_to_remove, "")

# 3. Add to Filter Panel (Third Row)
# We will insert it right after the second row in the filter panel.
# The second row ends with:
#       <div style="font-size: 12px; color: var(--text-muted);">
#         当前筛选结果: <b id="visibleDocCount" style="color: var(--accent-cyan);">{{ documents|length }}</b> / {{ documents|length }} 份
#       </div>
#     </div>
#   </div>

filter_html_to_add = """
    <div style="border-top: 1px dashed var(--border-subtle);"></div>

    <!-- 第三排：下拉框筛选 (业务分类与业务期间) -->
    <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px;">
      <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
        
        <div style="display: flex; align-items: center; gap: 8px;">
          <span class="filter-group-title" style="margin-right: 0;">🏷️ 业务分类:</span>
          <select id="categoryFilter" onchange="applyFilters()" class="form-select" style="padding: 4px 10px; font-size: 12px; border-radius: 6px; width: 140px; background: rgba(11, 19, 38, 0.8);">
            <option value="all">全部分类</option>
          </select>
        </div>

        <div style="display: flex; align-items: center; gap: 8px;">
          <span class="filter-group-title" style="margin-right: 0;">📅 业务期间:</span>
          <select id="periodFilter" onchange="applyFilters()" class="form-select" style="padding: 4px 10px; font-size: 12px; border-radius: 6px; width: 140px; background: rgba(11, 19, 38, 0.8);">
            <option value="all">全部期间</option>
          </select>
        </div>
        
      </div>
    </div>
"""

# Find the place to inject
anchor = """      <div style="font-size: 12px; color: var(--text-muted);">
        当前筛选结果: <b id="visibleDocCount" style="color: var(--accent-cyan);">{{ documents|length }}</b> / {{ documents|length }} 份
      </div>
    </div>"""
content = content.replace(anchor, anchor + filter_html_to_add)

# 4. Update JavaScript to populate selects on page load
js_populate = """
document.addEventListener('DOMContentLoaded', () => {
  updateFilterCounts();
  applyFilters();
});
"""
new_js_populate = """
document.addEventListener('DOMContentLoaded', () => {
  // Populate category and period dropdowns
  const rows = document.querySelectorAll('.project-doc-row');
  const catSet = new Set();
  const periodSet = new Set();
  
  rows.forEach(row => {
    const cat = row.getAttribute('data-category') || '';
    const period = row.getAttribute('data-period') || '';
    if (cat && cat !== 'None') catSet.add(cat);
    if (period && period !== 'None') periodSet.add(period);
  });
  
  const catFilter = document.getElementById('categoryFilter');
  Array.from(catSet).sort().forEach(c => {
    const opt = document.createElement('option');
    opt.value = c;
    // We could map english to chinese here, but backend already uses names or we can just show english for now.
    // Wait, the template uses `d.business_category | category_name`. We can fetch the text from the rows instead, but the rows have `data-category="{{ d.business_category or '' }}"` which is the code.
    // Let's rely on mapping it on the frontend, or better: just use the inner text of the badge if we kept it, but we removed it.
    // The user's Jinja filter is `category_name`. Let's just output it in the HTML template directly using Jinja2 loops.
    // We will do that below by modifying the injected HTML instead!
  });
});
"""
# Actually, it's better to just use Jinja2 to populate the options!

with open("source_code/0.2_RAG系统/project-rag-v1.1/app/templates/project.html", "w") as f:
    f.write(content)
