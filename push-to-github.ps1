<#
.SYNOPSIS
  Connect this local repo to a GitHub remote and push it, activating the
  GitHub Actions cloud backup watcher.

.DESCRIPTION
  Run this AFTER you've created an empty PRIVATE repo on github.com
  (no README/license). It wires up the 'origin' remote and pushes 'main'.
  If the GitHub CLI ('gh') is installed and authenticated, it will also set
  the NTFY_TOPIC repo secret for you (read from config.json).

.EXAMPLE
  .\push-to-github.ps1 yourname/hyrox-seoul-alert

.EXAMPLE
  .\push-to-github.ps1 https://github.com/yourname/hyrox-seoul-alert.git
#>
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Repo
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# Accept either "owner/repo" or a full URL.
if ($Repo -match '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
    $RemoteUrl = "https://github.com/$Repo.git"
} else {
    $RemoteUrl = $Repo
}

if (-not (Test-Path ".git")) {
    Write-Error "No git repo here. Run this from the Hyrox Alert folder."
}

Write-Host "Remote URL: $RemoteUrl" -ForegroundColor Cyan

# Add or update 'origin'.
$existing = (git remote 2>$null)
if ($existing -contains "origin") {
    git remote set-url origin $RemoteUrl
    Write-Host "Updated existing 'origin'." -ForegroundColor Yellow
} else {
    git remote add origin $RemoteUrl
    Write-Host "Added 'origin'." -ForegroundColor Green
}

# Make sure we're on main, then push.
git branch -M main
Write-Host "`nPushing to GitHub..." -ForegroundColor Cyan
git push -u origin main
if ($LASTEXITCODE -ne 0) {
    Write-Error "Push failed. Check that the repo exists and you have access."
}
Write-Host "Pushed." -ForegroundColor Green

# Best-effort: set the NTFY_TOPIC secret via gh, if available.
$gh = Get-Command gh -ErrorAction SilentlyContinue
if ($gh) {
    try {
        $topic = (Get-Content config.json -Raw | ConvertFrom-Json).ntfy.topic
        if ($topic) {
            gh secret set NTFY_TOPIC --body "$topic"
            if ($LASTEXITCODE -eq 0) {
                Write-Host "Set repo secret NTFY_TOPIC." -ForegroundColor Green
            }
        }
    } catch {
        Write-Host "Could not set the secret automatically (set it manually below)." -ForegroundColor Yellow
    }
} else {
    Write-Host "`n'gh' not installed - set the secret manually:" -ForegroundColor Yellow
}

Write-Host @"

Next steps (if not done automatically):
  1. On github.com -> your repo -> Settings -> Secrets and variables -> Actions
     -> New repository secret: name = NTFY_TOPIC
        value = $((Get-Content config.json -Raw | ConvertFrom-Json).ntfy.topic)
  2. Open the Actions tab and enable workflows.
The cloud watcher then runs every 5 minutes on GitHub's servers.
"@ -ForegroundColor Cyan
