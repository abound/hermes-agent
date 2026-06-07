# Install remote-debugger profile from hermes-agent repo.
# Run from repo root: .\examples\remote-debugger\install-profile.ps1

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$ExampleDir = $PSScriptRoot
$ProfileDir = Join-Path $env:USERPROFILE ".hermes\profiles\remote-debugger"
$SkillDest = Join-Path $ProfileDir "skills\software-development\remote-ai-debugger"

New-Item -ItemType Directory -Force -Path $SkillDest | Out-Null

Copy-Item (Join-Path $ExampleDir "config.yaml.example") (Join-Path $ProfileDir "config.yaml") -Force
Copy-Item (Join-Path $ExampleDir "mcp_servers.example.yaml") (Join-Path $ProfileDir "mcp_servers.fragment.yaml") -Force
Copy-Item (Join-Path $ExampleDir ".env.example") (Join-Path $ProfileDir ".env") -Force
Copy-Item (Join-Path $RepoRoot "skills\software-development\remote-ai-debugger\SKILL.md") (Join-Path $SkillDest "SKILL.md") -Force
Copy-Item (Join-Path $ExampleDir "README.zh.md") (Join-Path $ProfileDir "README.zh.md") -Force
Copy-Item (Join-Path $ExampleDir "PLAN.zh.md") (Join-Path $ProfileDir "PLAN.zh.md") -Force
Copy-Item (Join-Path $ExampleDir "REQUIREMENTS.zh.md") (Join-Path $ProfileDir "REQUIREMENTS.zh.md") -Force

Write-Host "Installed profile to $ProfileDir"
Write-Host "Next: edit .env (TERMINAL_SSH_*), then: hermes -p remote-debugger doctor"
