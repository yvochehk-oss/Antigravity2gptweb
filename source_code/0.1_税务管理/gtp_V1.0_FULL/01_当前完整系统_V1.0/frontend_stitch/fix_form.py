import re

with open('src/components/TaxPlanningView.tsx', 'r') as f:
    content = f.read()

old_use_effect = """  useEffect(() => {
    fetchPenetration(numericId);
    handleRunPlanning();
  }, [currentProjId]);"""

new_use_effect = """  useEffect(() => {
    // 切换项目时，动态更新默认的业务包名称和金额，给用户直观的联动反馈
    setPackageName(`${currentProject.name} - Q3待规划综合包`);
    setPackageAmount(Math.floor(currentProject.remainingBudget * 0.15 / 1000000) * 1000000);
    
    fetchPenetration(numericId);
    handleRunPlanning();
  }, [currentProjId]);"""

content = content.replace(old_use_effect, new_use_effect)

with open('src/components/TaxPlanningView.tsx', 'w') as f:
    f.write(content)
