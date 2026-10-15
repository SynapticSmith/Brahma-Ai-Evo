<#
.SYNOPSIS
    Vendors the Live2D Cubism runtime used by assets/avatar_live2d/index.html.

.DESCRIPTION
    The Live2D avatar page needs three things that are deliberately NOT committed
    to the repository:

      1. vendor/live2dcubismcore.min.js   the Cubism Core (closed-source but
                                          redistributable per its own header).
      2. vendor/framework.js              an esbuild bundle of the Live2D
                                          CubismWebFramework (open license) plus
                                          the official sample's LAppModel and a
                                          small host shim.
      3. vendor/Shaders/WebGL/*.vert|frag the shaders the framework loads at
                                          runtime.

    All three are regenerable from public upstreams with pinned commits, so this
    script reproduces them instead of bloating the repo with ~800 KB of binaries.
    A fresh clone only needs: git, Node.js (for npx/esbuild) and network access.

    The script is idempotent. Pass -Force to rebuild from scratch.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\setup_live2d_cubism.ps1
#>
param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

# Pinned upstream revisions. Bumping these is the only way the build changes.
$FrameworkRepo = 'https://github.com/Live2D/CubismWebFramework.git'
$FrameworkSha  = 'd4da0aa07e47d2c1e4f5fa7ea6047861ea5e5d0b'  # "Update to Cubism 5 SDK for Web R5"
$SamplesRepo   = 'https://github.com/Live2D/CubismWebSamples.git'
$SamplesSha    = 'b1de66b0b1f1cb881d95fb6158622aeb6a2827bd'
# Cubism Core 6.0.1 (Cubism 5.3). The legacy /sdk-web/cubismcore/ URL serves
# Core 5.1.0, which cannot load moc3 version 6 models (e.g. the Ren sample).
$CoreUrl       = 'https://cubism.live2d.com/sdk-web/core/06/live2dcubismcore.min.js'

$Repo      = Split-Path -Parent $PSScriptRoot
$VendorDir = Join-Path $Repo 'assets\avatar_live2d\vendor'
$ShaderDir = Join-Path $VendorDir 'Shaders\WebGL'
$CorePath  = Join-Path $VendorDir 'live2dcubismcore.min.js'
$FwJsPath  = Join-Path $VendorDir 'framework.js'

# Clone cache lives outside the repo so it never shows up in git status.
$CacheRoot = Join-Path $env:LOCALAPPDATA 'BrahmaAI\live2d-build'
$FwDir     = Join-Path $CacheRoot 'CubismWebFramework'
$SamplesDir = Join-Path $CacheRoot 'CubismWebSamples'
$BuildDir  = Join-Path $CacheRoot 'bundle'

function Write-Step([string]$Message) { Write-Host "[live2d] $Message" -ForegroundColor Cyan }
function Write-Warn([string]$Message) { Write-Host "[live2d] $Message" -ForegroundColor Yellow }

function New-Dir([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Assert-Command([string]$Name, [string]$Hint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "'$Name' is required but was not found on PATH. $Hint"
    }
}

function Sync-Repo([string]$Url, [string]$Sha, [string]$Dir, [string]$Label) {
    if ((Test-Path -LiteralPath (Join-Path $Dir '.git')) -and -not $Force) {
        $head = (git -C $Dir rev-parse HEAD 2>$null)
        if ($head -and $head.Trim() -eq $Sha) {
            Write-Step "$Label already at pinned commit"
            return
        }
    }
    Write-Step "Fetching $Label @ $($Sha.Substring(0,8))..."
    Remove-Item -LiteralPath $Dir -Recurse -Force -ErrorAction SilentlyContinue
    New-Dir $Dir
    git -C $Dir init --quiet
    git -C $Dir remote add origin $Url
    git -C $Dir fetch --quiet --depth 1 origin $Sha
    git -C $Dir checkout --quiet FETCH_HEAD
}

New-Dir $VendorDir
New-Dir $ShaderDir

# --- 1. Cubism Core ---------------------------------------------------------
if ($Force -or -not (Test-Path -LiteralPath $CorePath)) {
    Write-Step 'Downloading Cubism Core...'
    Invoke-WebRequest -Uri $CoreUrl -OutFile $CorePath -UseBasicParsing
} else {
    Write-Step 'Cubism Core already present'
}

# --- 2. framework.js --------------------------------------------------------
if ($Force -or -not (Test-Path -LiteralPath $FwJsPath)) {
    Assert-Command git 'Install git (https://git-scm.com).'
    Assert-Command node 'Install Node.js (https://nodejs.org) - esbuild runs via npx.'

    Sync-Repo $FrameworkRepo $FrameworkSha $FwDir 'CubismWebFramework'
    Sync-Repo $SamplesRepo $SamplesSha $SamplesDir 'CubismWebSamples'

    $SampleSrc = Join-Path $SamplesDir 'Samples\TypeScript\Demo\src'
    $Src = Join-Path $BuildDir 'src'
    if (-not (Test-Path -LiteralPath $SampleSrc)) {
        throw "Expected sample sources were not found at $SampleSrc"
    }

    Write-Step 'Assembling bundle sources...'
    Remove-Item -LiteralPath $BuildDir -Recurse -Force -ErrorAction SilentlyContinue
    New-Dir $Src

    'lappdefine', 'lapppal', 'lappglmanager', 'lapptexturemanager',
    'lappwavfilehandler', 'lappmodel' | ForEach-Object {
        Copy-Item -LiteralPath (Join-Path $SampleSrc "$_.ts") -Destination $Src -Force
    }

    # The page hosts a single canvas and drives the frame loop itself, so it does
    # not need the sample's app shell (LAppDelegate/LAppLive2DManager/LAppView).
    # LAppModel imports LAppDelegate, so a stub keeps that import resolvable
    # without pulling in the whole sample. Types are erased by esbuild; only the
    # runtime members below need to be real.
    $DelegateStub = @'
// Minimal stand-in for the sample's LAppDelegate. LAppModel imports it but
// never calls into it in this bundle; the stub only satisfies the module graph.
export class LAppDelegate {}
'@
    Set-Content -LiteralPath (Join-Path $Src 'lappdelegate.ts') -Value $DelegateStub -Encoding UTF8

    $SubdelegateShim = @'
/**
 * Host shim for LAppModel: exposes exactly the surface LAppModel uses
 * (canvas, GL manager, texture manager, bound framebuffer) without the
 * sample's multi-canvas / view / resize plumbing.
 */
import { LAppGlManager } from './lappglmanager';
import { LAppTextureManager } from './lapptexturemanager';

export class LAppSubdelegate {
    public constructor(
        canvas: HTMLCanvasElement,
        glManager: LAppGlManager,
        textureManager: LAppTextureManager
    ) {
        this._canvas = canvas;
        this._glManager = glManager;
        this._textureManager = textureManager;
        this._frameBuffer = null;
    }

    public getCanvas(): HTMLCanvasElement { return this._canvas; }
    public getGlManager(): LAppGlManager { return this._glManager; }
    public getTextureManager(): LAppTextureManager { return this._textureManager; }
    public getFrameBuffer(): WebGLFramebuffer { return this._frameBuffer; }
    public setFrameBuffer(frameBuffer: WebGLFramebuffer): void { this._frameBuffer = frameBuffer; }

    private _canvas: HTMLCanvasElement;
    private _glManager: LAppGlManager;
    private _textureManager: LAppTextureManager;
    private _frameBuffer: WebGLFramebuffer;
}
'@
    Set-Content -LiteralPath (Join-Path $Src 'lappsubdelegate.ts') -Value $SubdelegateShim -Encoding UTF8

    $Entry = @'
// Public surface of the vendored Live2D runtime, exposed on the global
// `Live2DCubismFramework` for the avatar page.
export * as LAppDefine from './lappdefine';
export { LAppPal } from './lapppal';
export { LAppGlManager } from './lappglmanager';
export { LAppTextureManager, TextureInfo } from './lapptexturemanager';
export { LAppModel } from './lappmodel';
export { LAppSubdelegate } from './lappsubdelegate';
export {
    CubismFramework,
    Option,
    LogLevel
} from '@framework/live2dcubismframework';
export { CubismMatrix44 } from '@framework/math/cubismmatrix44';
export { CubismModelSettingJson } from '@framework/cubismmodelsettingjson';
export { CubismDefaultParameterId } from '@framework/cubismdefaultparameterid';
export { CubismUserModel } from '@framework/model/cubismusermodel';
export { CubismMoc } from '@framework/model/cubismmoc';
export {
    CubismWebGLOffscreenManager
} from '@framework/rendering/cubismoffscreenmanager';
'@
    Set-Content -LiteralPath (Join-Path $Src 'entry.ts') -Value $Entry -Encoding UTF8

    # Point the framework at the page's shader location and quiet its logging.
    $DefinePath = Join-Path $Src 'lappdefine.ts'
    $Define = Get-Content -LiteralPath $DefinePath -Raw
    $Define = $Define.Replace("'../../Framework/Shaders/WebGL/'", "'vendor/Shaders/WebGL/'")
    $Define = $Define.Replace('export const DebugLogEnable = true;', 'export const DebugLogEnable = false;')
    $Define = $Define.Replace(
        'export const CubismLoggingLevel: LogLevel = LogLevel.LogLevel_Verbose;',
        'export const CubismLoggingLevel: LogLevel = LogLevel.LogLevel_Error;'
    )
    Set-Content -LiteralPath $DefinePath -Value $Define -Encoding UTF8

    Write-Step 'Bundling framework.js with esbuild...'
    $FwSrc = (Join-Path $FwDir 'src').Replace('\', '/')
    Push-Location $BuildDir
    try {
        npx --yes esbuild 'src/entry.ts' `
            --bundle --format=iife --global-name=Live2DCubismFramework `
            --platform=browser --target=es2018 `
            --alias:@framework=$FwSrc `
            --outfile=framework.js --log-level=warning
        if ($LASTEXITCODE -ne 0) { throw "esbuild failed with exit code $LASTEXITCODE" }
    } finally {
        Pop-Location
    }

    $Built = Join-Path $BuildDir 'framework.js'
    if (-not (Test-Path -LiteralPath $Built)) { throw 'esbuild did not produce framework.js' }
    node --check $Built
    if ($LASTEXITCODE -ne 0) { throw 'framework.js failed syntax check' }
    Copy-Item -LiteralPath $Built -Destination $FwJsPath -Force

    # --- 3. Shaders ---------------------------------------------------------
    Write-Step 'Copying shaders...'
    $FwShaders = Join-Path $FwDir 'Shaders\WebGL'
    Get-ChildItem -LiteralPath $FwShaders -Filter '*.vert' | Copy-Item -Destination $ShaderDir -Force
    Get-ChildItem -LiteralPath $FwShaders -Filter '*.frag' | Copy-Item -Destination $ShaderDir -Force
} else {
    Write-Step 'framework.js already present (use -Force to rebuild)'
}

$coreBytes = (Get-Item -LiteralPath $CorePath).Length
$fwBytes   = (Get-Item -LiteralPath $FwJsPath).Length
$shaderCount = (Get-ChildItem -LiteralPath $ShaderDir -File).Count
Write-Host "[live2d] core=$coreBytes bytes  framework=$fwBytes bytes  shaders=$shaderCount" -ForegroundColor Green
Write-Host '[live2d] Live2D runtime ready.' -ForegroundColor Green
