param(
    [string]$RootDir = "F:\073_成都建工_V3.1_Windows"
)

# 1. Kill llama-server
Stop-Process -Name "llama-server" -Force -ErrorAction SilentlyContinue

# 2. Kill listeners on ports 8921, 8922, 8933, 5173
foreach ($p in 8921, 8922, 8933, 5173) {
    $conns = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
    }
}

Write-Output "ALL_SERVICES_STOPPED"
