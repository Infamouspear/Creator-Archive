$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$bundle = @()
if ((Test-Path tools) -and (Get-ChildItem tools -File)) {
    $bundle += @('--add-data', 'tools;tools')
}
python -m PyInstaller --noconfirm --windowed --name CreatorArchive @bundle app.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
$compiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
$iscc = if ($compiler) { $compiler.Source } else { Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe' }
& $iscc installer.iss
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup failed.' }
