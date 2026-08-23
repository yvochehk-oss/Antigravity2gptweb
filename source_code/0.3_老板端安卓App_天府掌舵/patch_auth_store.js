import fs from 'fs'

const path = 'src/stores/auth.store.js'
let content = fs.readFileSync(path, 'utf8')

// Add EXECUTIVE role
content = content.replace(
  "ADMIN: 'admin',",
  "ADMIN: 'admin',\n  EXECUTIVE: 'executive',"
)

// Add view permissions
content = content.replace(
  "MANAGE_SETTINGS: 'settings:manage'",
  "MANAGE_SETTINGS: 'settings:manage',\n  VIEW_COCKPIT: 'view:cockpit',\n  VIEW_PROJECTS: 'view:projects',\n  VIEW_COMPANIES: 'view:companies',\n  USE_COPILOT: 'use:copilot'"
)

// Update ROLE_PERMISSIONS
content = content.replace(
  "[ROLES.OPERATOR]: [PERMISSIONS.VIEW_ALL_DATA]",
  "[ROLES.OPERATOR]: [PERMISSIONS.VIEW_ALL_DATA],\n  [ROLES.EXECUTIVE]: [PERMISSIONS.VIEW_COCKPIT, PERMISSIONS.VIEW_PROJECTS, PERMISSIONS.VIEW_COMPANIES, PERMISSIONS.USE_COPILOT, PERMISSIONS.MANAGE_SETTINGS]"
)
content = content.replace(
  "[ROLES.ADMIN]: Object.values(PERMISSIONS),",
  "[ROLES.ADMIN]: Object.values(PERMISSIONS),"
) // Actually Object.values will include the new ones automatically

fs.writeFileSync(path, content)
