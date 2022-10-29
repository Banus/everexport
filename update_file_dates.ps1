# Get a listing of all the Markdown files within the given folder and all subfolders.
# modified from: https://forum.obsidian.md/t/preserve-creation-dates-when-using-obsidian-sync/24818/5
param ( $Dir = '.')
$files = Get-ChildItem $Dir -Recurse -Name -Filter "*.md"

foreach ($file in $files) {
    $file = $Dir + "\" + $file
    $createdDate = Get-Content -LiteralPath $file | Select -Index 3
    $createdDate = $createdDate -replace 'created: ',''
    
    if ([string]$createdDate -as [DateTime]) {  
        $(Get-Item -LiteralPath $file).creationtime=$("$createdDate")
    }
    else { Write-Host "Error setting creation date for: $file" }

    $modifiedDate = Get-Content -LiteralPath $file | Select -Index 2
    $modifiedDate = $modifiedDate -replace 'updated: ',''
    
    if ([string]$modifiedDate -as [DateTime]) {  
        $(Get-Item -LiteralPath $file).LastWriteTime=$("$modifiedDate")
    }
    else { Write-Host "Error setting modification date for: $file" }
}
Write-Host "Finished running. Press any key to continue..."
$Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
