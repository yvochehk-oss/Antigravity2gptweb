import re

with open('src/components/TaxPlanningView.tsx', 'r') as f:
    content = f.read()

# Make select bigger
old_select = """          <select
            value={currentProjId}
            onChange={(e) => {
              setCurrentProjId(e.target.value);
              if (onSelectProject) onSelectProject(e.target.value);
            }}
            className="bg-[#0b1326] border border-[#4cd7f6]/60 text-[#dae2fd] text-[13px] font-bold rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-[#4cd7f6]/40 cursor-pointer min-w-[280px] max-w-[360px] truncate shadow-inner"
          >"""

new_select = """          <select
            value={currentProjId}
            onChange={(e) => {
              setCurrentProjId(e.target.value);
              if (onSelectProject) onSelectProject(e.target.value);
            }}
            className="bg-[#0b1326] border border-[#4cd7f6]/60 text-[#dae2fd] text-[16px] font-bold rounded-lg px-4 py-2.5 focus:outline-none focus:ring-2 focus:ring-[#4cd7f6]/40 cursor-pointer min-w-[320px] max-w-[420px] truncate shadow-inner tracking-wide"
          >"""

content = content.replace(old_select, new_select)

old_label = """          <div className="flex items-center gap-1.5 text-[11.5px] font-bold text-[#4cd7f6]">
            <Building2 className="w-3.5 h-3.5 text-[#4cd7f6]" />
            <span>当前筹划标段工程</span>
          </div>"""

new_label = """          <div className="flex items-center gap-1.5 text-[13px] font-bold text-[#4cd7f6] mb-0.5">
            <Building2 className="w-4 h-4 text-[#4cd7f6]" />
            <span>当前筹划标段工程</span>
          </div>"""

content = content.replace(old_label, new_label)

with open('src/components/TaxPlanningView.tsx', 'w') as f:
    f.write(content)
