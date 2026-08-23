import re

with open('src/components/TaxPlanningView.tsx', 'r') as f:
    content = f.read()

# Replace the classes for inputs and selects in the form grid
input_class_old1 = 'className="w-full bg-[#131b2e] border border-[#444653]/50 rounded-lg px-3 py-2 text-[#dae2fd] text-[13px] focus:outline-none focus:border-[#a78bfa]/60"'
input_class_new1 = 'className="w-full h-9 bg-[#131b2e] border border-[#444653]/50 rounded-lg px-3 text-[#dae2fd] text-[13px] focus:outline-none focus:border-[#a78bfa]/60 box-border"'

input_class_old2 = 'className="w-full bg-[#131b2e] border border-[#444653]/50 rounded-lg px-3 py-2 text-[#dae2fd] font-mono-num text-[13px] focus:outline-none focus:border-[#a78bfa]/60"'
input_class_new2 = 'className="w-full h-9 bg-[#131b2e] border border-[#444653]/50 rounded-lg px-3 text-[#dae2fd] font-mono-num text-[13px] focus:outline-none focus:border-[#a78bfa]/60 box-border"'

input_class_old3 = 'className="w-full bg-[#131b2e] border border-[#444653]/50 rounded-lg px-3 py-2 text-[#dae2fd] font-mono-num text-[13px]"'
input_class_new3 = 'className="w-full h-9 bg-[#131b2e] border border-[#444653]/50 rounded-lg px-3 text-[#dae2fd] font-mono-num text-[13px] focus:outline-none focus:border-[#a78bfa]/60 box-border"'

select_class_old1 = 'className="w-full bg-[#131b2e] border border-[#444653]/50 rounded-lg px-3 py-2 text-[#dae2fd] text-[13px] focus:outline-none focus:border-[#a78bfa]/60 cursor-pointer"'
select_class_new1 = 'className="w-full h-9 bg-[#131b2e] border border-[#444653]/50 rounded-lg px-3 text-[#dae2fd] text-[13px] focus:outline-none focus:border-[#a78bfa]/60 cursor-pointer box-border"'

div_class_old1 = 'className="w-full bg-[#131b2e]/60 border border-[#444653]/30 rounded-lg px-3 py-2 text-[#8e909f] text-[12px] flex items-center justify-between"'
div_class_new1 = 'className="w-full h-9 bg-[#131b2e]/60 border border-[#444653]/30 rounded-lg px-3 text-[#8e909f] text-[12px] flex items-center justify-between box-border"'

content = content.replace(input_class_old1, input_class_new1)
content = content.replace(input_class_old2, input_class_new2)
content = content.replace(input_class_old3, input_class_new3)
content = content.replace(select_class_old1, select_class_new1)
content = content.replace(div_class_old1, div_class_new1)

with open('src/components/TaxPlanningView.tsx', 'w') as f:
    f.write(content)
