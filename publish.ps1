# publish.ps1 — 一键把本插件发布到 GitHub（含自动建仓库）
#
# ── 最省事的用法（推荐）────────────────────────────────────────────
#   1) 去 https://github.com/settings/tokens?type=beta 生成 fine-grained PAT
#        Repository access : All repositories
#        Permissions       : Contents = Read and write
#                            Administration = Read and write   ← 自动建仓库要用
#   2) 在本目录打开 PowerShell，粘贴下面三行（把 token 和用户名换成你自己的）：
#        $env:GITHUB_TOKEN = "github_pat_xxx"
#        cd C:\Users\25868\Documents\deepseek-harness\default-workspace\index-fund-guide-plugin
#        .\publish.ps1 -Owner <你的GitHub用户名> -CreateRepo
#   3) 跑完会打印仓库地址。成功后立刻去 GitHub 把那个 PAT 删掉。
#
# ── 仓库已经建好的用法 ──────────────────────────────────────────
#   .\publish.ps1 -RepoUrl https://github.com/<用户名>/dsh-index-fund-guide.git
#
# ── 不想用 PAT（走浏览器登录）────────────────────────────────────
#   先在网页上建好空仓库（不要勾 README/gitignore/license），然后：
#   .\publish.ps1 -RepoUrl https://github.com/<用户名>/dsh-index-fund-guide.git
#   push 时 Git Credential Manager 会弹浏览器让你授权。
#
# 参数说明：
#   -Owner        GitHub 用户名（会写进 package.json 的 repository/homepage）
#   -RepoName     仓库名，默认 dsh-index-fund-guide
#   -CreateRepo   用 GitHub API 新建仓库（需要 $env:GITHUB_TOKEN）
#   -Private      配合 -CreateRepo，建成私有仓库
#   -RepoUrl      已有仓库的地址（与 -CreateRepo 二选一）
#   -UserName/-UserEmail  提交者身份；不填则沿用本仓库/全局 git 配置
#   -Branch       分支名，默认 main
#   -UseToken     push 时用 $env:GITHUB_TOKEN 认证（token 不会写入 .git/config）

[CmdletBinding(DefaultParameterSetName = "url")]
param(
    [Parameter(ParameterSetName = "url", Mandatory = $true)]
    [string]$RepoUrl,

    [Parameter(ParameterSetName = "create", Mandatory = $true)]
    [string]$Owner,

    [string]$RepoName = "dsh-index-fund-guide",
    [Parameter(ParameterSetName = "create")]
    [switch]$CreateRepo,
    [switch]$Private,
    [string]$UserName,
    [string]$UserEmail,
    [string]$Branch = "main",
    [switch]$UseToken
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Find-Git {
    foreach ($p in @("C:\Program Files\Git\cmd\git.exe", "$env:LOCALAPPDATA\Programs\Git\cmd\git.exe")) {
        if (Test-Path $p) { return $p }
    }
    $c = Get-Command git -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    throw "找不到 git。请先安装：winget install Git.Git"
}

function Set-Utf8NoBom([string]$path, [string]$text) {
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($path, $text, $enc)
}

function Fill-Placeholders([string]$owner, [string]$author, [string]$repo) {
    if (-not $owner -or -not $repo) {
        Write-Host "[0/6] 跳过 package.json 元信息替换（未提供 -Owner / 仓库名）"
        return
    }
    $pkg = Join-Path $PSScriptRoot "package.json"
    $raw = [System.IO.File]::ReadAllText($pkg, [System.Text.Encoding]::UTF8)
    $before = $raw
    $raw = $raw.Replace("PLACEHOLDER_OWNER", $owner)
    $raw = $raw.Replace("PLACEHOLDER_AUTHOR", $author)
    $raw = $raw.Replace("dsh-index-fund-guide.git", "$repo.git")
    if ($raw -ne $before) {
        Set-Utf8NoBom $pkg $raw
        Write-Host "[0/6] package.json 元信息已填: $owner/$repo"
    } else {
        Write-Host "[0/6] package.json 元信息已是最终值"
    }
}

$git = Find-Git
Write-Host "[1/6] git = $git  ($(& $git --version))"

# ---- 身份 ----
if (-not $UserName) { $UserName = (& $git config user.name) }
if (-not $UserEmail) { $UserEmail = (& $git config user.email) }
if (-not $UserName) { $UserName = (& $git config --global user.name) }
if (-not $UserEmail) { $UserEmail = (& $git config --global user.email) }
if (-not $UserName) { $UserName = Read-Host "提交者姓名" }
if (-not $UserEmail) { $UserEmail = Read-Host "提交者邮箱" }
& $git config user.name $UserName
& $git config user.email $UserEmail
Write-Host "[2/6] 提交身份: $UserName <$UserEmail>"

# ---- 建仓库 / 定地址 ----
if ($CreateRepo) {
    if (-not $env:GITHUB_TOKEN) { throw "用了 -CreateRepo 但没有设置 `$env:GITHUB_TOKEN" }
    $repo = $RepoName
    $body = @{
        name        = $repo
        description = "《指数基金投资指南》(银行螺丝钉) 的估值—定投—卖出体系，DSH 插件"
        private     = [bool]$Private
        has_issues  = $true
        has_wiki    = $false
    } | ConvertTo-Json
    $headers = @{
        Authorization          = "token $($env:GITHUB_TOKEN)"
        Accept                 = "application/vnd.github+json"
        "X-GitHub-Api-Version" = "2022-11-28"
        "User-Agent"           = "publish.ps1"
    }
    Write-Host "[3/6] 正在 GitHub API 创建仓库 $Owner/$repo …"
    try {
        $r = Invoke-RestMethod -Method Post -Uri "https://api.github.com/user/repos" -Headers $headers -Body $body -ContentType "application/json; charset=utf-8"
        Write-Host "      已创建: $($r.html_url)"
        $RepoUrl = $r.clone_url
    } catch {
        $msg = $_.Exception.Message
        if ("$msg" -match "422") {
            Write-Host "      仓库已存在（422），改用已有仓库继续"
            $RepoUrl = "https://github.com/$Owner/$repo.git"
        } else {
            throw "建仓库失败: $msg"
        }
    }
    Fill-Placeholders $Owner $UserName $repo
} else {
    # 从 URL 里反推 owner / 仓库名，推不出就跳过元信息替换
    $m = [regex]::Match($RepoUrl, 'github\.com[:/]+([^/]+)/([^/]+?)(\.git)?$')
    if ($m.Success -and -not $Owner) { $Owner = $m.Groups[1].Value }
    if ($m.Success -and $RepoName -eq "dsh-index-fund-guide") { $RepoName = $m.Groups[2].Value }
    Fill-Placeholders $Owner $UserName $RepoName
}

# ---- 初始化 / 提交 ----
if (-not (Test-Path ".git")) {
    & $git init -b $Branch | Out-Null
    Write-Host "[4/6] git init (branch=$Branch)"
} else {
    Write-Host "[4/6] 已有 .git"
}
& $git add -A
$pending = & $git status --porcelain
if ($pending) {
    $ver = (Get-Content package.json -Raw -Encoding UTF8 | ConvertFrom-Json).version
    & $git commit -q -m "release: v$ver"
    if ($LASTEXITCODE -ne 0) { throw "提交失败" }
    Write-Host "      已提交: release: v$ver"
} else {
    Write-Host "      无变更，跳过提交"
}

# ---- remote ----
$known = (& $git remote) 2>$null
if ($known -contains "origin") { & $git remote set-url origin $RepoUrl } else { & $git remote add origin $RepoUrl }
Write-Host "[5/6] remote origin -> $RepoUrl"

# ---- push ----
$pushUrl = $RepoUrl
if ($UseToken -or ($env:GITHUB_TOKEN -and $RepoUrl -match '^https://')) {
    if ($env:GITHUB_TOKEN -and $RepoUrl -match '^https://') {
        $pushUrl = $RepoUrl -replace '^https://', ("https://x-access-token:" + $env:GITHUB_TOKEN + "@")
    }
}
Write-Host "[6/6] 推送中 …"
& $git push -u $pushUrl "HEAD:$Branch"
if ($LASTEXITCODE -ne 0) {
    throw "推送失败。检查：token 是否含 Contents 写权限、仓库是否为空、网络是否可达 github.com。"
}
& $git remote set-url origin $RepoUrl   # 抹掉可能内嵌的 token
Write-Host ""
Write-Host "完成 → $RepoUrl"
Write-Host "别人安装本插件： pnpm add $RepoUrl"
Write-Host "记得去 GitHub 删除本次用的 PAT。"
