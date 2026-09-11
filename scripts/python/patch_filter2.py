import re

with open("source_code/0.2_RAG系统/project-rag-v1.1/app/templates/project.html", "r") as f:
    content = f.read()

# Replace the injected HTML with Jinja2 populated loops
old_html = """        <div style="display: flex; align-items: center; gap: 8px;">
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
        </div>"""

new_html = """        <div style="display: flex; align-items: center; gap: 8px;">
          <span class="filter-group-title" style="margin-right: 0;">🏷️ 业务分类:</span>
          <select id="categoryFilter" onchange="applyFilters()" class="form-select" style="padding: 4px 10px; font-size: 12px; border-radius: 6px; width: 140px; background: rgba(11, 19, 38, 0.8);">
            <option value="all">全部分类</option>
            {% set categories = [] %}
            {% for d in documents %}
              {% if d.business_category and d.business_category not in categories %}
                {% set _ = categories.append(d.business_category) %}
              {% endif %}
            {% endfor %}
            {% for cat in categories|sort %}
            <option value="{{ cat }}">{{ cat | category_name }}</option>
            {% endfor %}
          </select>
        </div>

        <div style="display: flex; align-items: center; gap: 8px;">
          <span class="filter-group-title" style="margin-right: 0;">📅 业务期间:</span>
          <select id="periodFilter" onchange="applyFilters()" class="form-select" style="padding: 4px 10px; font-size: 12px; border-radius: 6px; width: 140px; background: rgba(11, 19, 38, 0.8);">
            <option value="all">全部期间</option>
            {% set periods = [] %}
            {% for d in documents %}
              {% if d.period and d.period not in periods %}
                {% set _ = periods.append(d.period) %}
              {% endif %}
            {% endfor %}
            {% for p in periods|sort(reverse=True) %}
            <option value="{{ p }}">{{ p }}</option>
            {% endfor %}
          </select>
        </div>"""

content = content.replace(old_html, new_html)

# Now inject the javascript filtering logic into applyFilters()
js_old = """    // 2. Scope filter
    let matchScope = true;
    if (currentSelectedScope === 'internal') {
      matchScope = !isExt;
    } else if (currentSelectedScope === 'external') {
      matchScope = isExt;
    }

    // 3. Search query"""

js_new = """    // 2. Scope filter
    let matchScope = true;
    if (currentSelectedScope === 'internal') {
      matchScope = !isExt;
    } else if (currentSelectedScope === 'external') {
      matchScope = isExt;
    }
    
    // 2.5 Dropdown filters
    const catVal = document.getElementById('categoryFilter').value;
    const periodVal = document.getElementById('periodFilter').value;
    const docCat = row.getAttribute('data-category') || '';
    const docPeriod = row.getAttribute('data-period') || '';
    
    let matchDropdown = true;
    if (catVal !== 'all' && docCat !== catVal) matchDropdown = false;
    if (periodVal !== 'all' && docPeriod !== periodVal) matchDropdown = false;

    // 3. Search query"""

content = content.replace(js_old, js_new)

# And finally, update the visibility logic
js_vis_old = """    if (matchType && matchScope && matchSearch) {"""
js_vis_new = """    if (matchType && matchScope && matchDropdown && matchSearch) {"""
content = content.replace(js_vis_old, js_vis_new)

with open("source_code/0.2_RAG系统/project-rag-v1.1/app/templates/project.html", "w") as f:
    f.write(content)
