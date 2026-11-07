# P4 bulk import: adapt ALL 607 agent defs to opencode subagents.
# ASCII-only (PS5.1). Preserves the 16 hand-tuned promoted agents; generates the
# remaining 591 from catalog.json + source corpus. UTF-8 no-BOM output.
$ErrorActionPreference = 'Stop'
$corpusRoot = 'C:\Users\Admin\610ClaudeSubagents'
$outRoot    = 'C:\Users\Admin\HarnessAgents\.opencode\agent'
$catalog    = Get-Content -LiteralPath 'C:\Users\Admin\HarnessAgents\hive\library\subagents\catalog.json' -Raw | ConvertFrom-Json
$utf8NoBom  = New-Object System.Text.UTF8Encoding($false)

# archetype -> tools to DENY (default-allow otherwise). Same set as P3.
$readonly = @('bash','webfetch','websearch','task','todowrite','skill','lsp','doom_loop','question')
$engineer = @('webfetch','websearch','task','todowrite','skill','lsp','doom_loop','question')
$docs     = @('webfetch','websearch','task','question','skill','lsp','doom_loop')
$research = @('question','skill','lsp','doom_loop')

# Preserve exact hand-tuned archetypes for the 16 already-promoted agents.
$preserve = @{
  'advanced-research-engine'        = $research
  'output-synthesis-refinement'     = $engineer
  'quality-review-loop-controller'  = $readonly
  'independent-verification-agent'  = $readonly
  'task-completion-assessor'        = $readonly
  'architecture-critique-agent'     = $readonly
  'architecture-analyst'            = $readonly
  'gap-consistency-checker-agent'   = $readonly
  'static-analysis-quality-agent'   = $readonly
  'edge-case-synthesizer'           = $engineer
  'backend-api-code-writer-agent'   = $engineer
  'frontend-ui-code-writer-agent'   = $engineer
  'test-code-writer-agent'          = $engineer
  'devops-automation-script-writer-agent' = $engineer
  'comprehensive-documentation-agent'     = $docs
  'codebase-qa-agent'               = $readonly
}

function Resolve-Archetype($tools, $slug) {
  # 1) explicit preserve map (16 P3 agents)
  if ($preserve.ContainsKey($slug)) { return $preserve[$slug] }
  # 2) tool-driven: clean tokens only (noise like 'Bash]' / '[Read' is ignored)
  $clean = @($tools | Where-Object { $_ -match '^[A-Za-z]+$' })
  $hasWeb  = ($clean | Where-Object { $_ -in @('WebFetch','WebSearch','webfetch','websearch') }).Count -gt 0
  $hasBash = ($clean | Where-Object { $_ -match '^bash$' -or $_ -eq 'Bash' }).Count -gt 0
  if ($hasWeb)  { return $research }
  if ($hasBash) { return $engineer }
  return $readonly
}

function Sanitize-Desc([string]$s) {
  $s = $s.Trim().Replace('"', "'")
  return '"' + $s + '"'
}

function Split-Frontmatter($raw) {
  $raw = $raw.TrimStart([char]0xFEFF)
  $raw = $raw -replace "`r`n", "`n" -replace "`r", "`n"
  $lines = $raw -split "`n"
  if ($lines.Length -lt 3 -or $lines[0] -ne '---') { return $null }
  $close = -1
  for ($i = 1; $i -lt $lines.Length; $i++) { if ($lines[$i] -eq '---') { $close = $i; break } }
  if ($close -lt 0) { return $null }
  return @{ fm = ($lines[1..($close-1)] -join "`n"); body = ($lines[($close+1)..($lines.Length-1)] -join "`n") }
}

$agents = @($catalog | Where-Object { $_.kind -eq 'agent' })
$built  = 0; $skipped = 0; $errors = 0; $noFence = 0; $manifest = @()
foreach ($a in $agents) {
  $slug = $a.slug
  $src  = Join-Path $corpusRoot $a.path
  if (-not (Test-Path -LiteralPath $src)) { Write-Error "MISSING SOURCE: $src"; $errors++; continue }
  if ($preserve.ContainsKey($slug) -and (Test-Path -LiteralPath (Join-Path $outRoot ($slug + '.md')))) { $skipped++; continue }

  $raw = [System.IO.File]::ReadAllText($src, [System.Text.Encoding]::UTF8)
  $split = Split-Frontmatter $raw
  if (-not $split) {
    # No leading --- fence (corpus has a '## Agent Metadata' style too). Descriptions
    # and tools already come from the catalog, so import the body verbatim.
    $split = @{ fm = ''; body = $raw.TrimStart([char]0xFEFF) }
    $noFence++
  }

  $desc = $a.description
  if (-not $desc) { Write-Error "NO DESCRIPTION: $src"; $errors++; continue }
  $desc = Sanitize-Desc $desc

  $arche = Resolve-Archetype $a.tools $slug
  $denyBlock = ($arche | ForEach-Object { "  $_`: deny" }) -join "`n"

  $header = @"
---
description: $desc
mode: subagent
permission:
$denyBlock
---

<!-- Adapted from the 610ClaudeSubagents corpus (source: $($a.path)) on 2026-10-10. Frontmatter normalized to opencode; tool surface translated to an opencode permission deny-map. Treat success-rate / speed / token figures in the source body as UNVERIFIED marketing claims: verify evidence before asserting any of them. -->
"@

  $out = $header + "`n`n" + $split.body.TrimStart()
  $dst = Join-Path $outRoot ($slug + '.md')
  [System.IO.File]::WriteAllText($dst, $out, $utf8NoBom)
  $manifest += [PSCustomObject]@{ slug = $slug; archetype = ($arche -join ','); src = $a.path; dst = $dst; bytes = (Get-Item -LiteralPath $dst).Length }
  $built++
}

$manifest | ForEach-Object { "{0,-58} {1,7} B  <-  {2}" -f (Split-Path $_.dst -Leaf), $_.bytes, $_.src }
Write-Output ""
Write-Output ("BUILT: {0}   SKIPPED(preserved): {1}   ERRORS: {2}   NO-FENCE(ex-verbatim): {3}   TOTAL agents: {4}" -f $built, $skipped, $errors, $noFence, $agents.Count)
Write-Output ("PLAIN FILES NOW IN " + $outRoot + ": " + (Get-ChildItem -LiteralPath $outRoot -File -Filter *.md).Count)