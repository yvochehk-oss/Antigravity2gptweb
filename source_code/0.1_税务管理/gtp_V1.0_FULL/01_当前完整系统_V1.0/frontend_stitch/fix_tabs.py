with open('src/components/TaxPlanningView.tsx', 'r') as f:
    content = f.read()

# Tab 1
old_tab1 = """className={`flex items-center gap-2 px-4 py-2 rounded-lg text-[13px] font-semibold transition-all cursor-pointer ${"""
new_tab1 = """className={`flex items-center gap-2.5 px-6 py-2.5 rounded-xl text-[16px] font-bold tracking-wide transition-all cursor-pointer ${"""

old_icon1 = """<Layers className="w-4 h-4" />"""
new_icon1 = """<Layers className="w-5 h-5" />"""

# Tab 2
old_tab2 = """className={`flex items-center gap-2 px-4 py-2 rounded-lg text-[13px] font-semibold transition-all cursor-pointer ${"""
new_tab2 = """className={`flex items-center gap-2.5 px-6 py-2.5 rounded-xl text-[16px] font-bold tracking-wide transition-all cursor-pointer ${"""

old_icon2 = """<TrendingUp className="w-4 h-4" />"""
new_icon2 = """<TrendingUp className="w-5 h-5" />"""

content = content.replace(old_tab1, new_tab1, 1) # Only first match, wait, actually let's just do them both at once if they are identical
content = content.replace(old_icon1, new_icon1)
content = content.replace(old_tab2, new_tab2) # the second match because the first one is replaced
content = content.replace(old_icon2, new_icon2)

with open('src/components/TaxPlanningView.tsx', 'w') as f:
    f.write(content)
