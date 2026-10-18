[CmdletBinding()]
param([Parameter(Mandatory=$true)][uri]$Url, [switch]$AllowHttpForLocalTest)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($Url.Scheme -ne 'https' -and -not $AllowHttpForLocalTest) { throw 'HTTPS is required. Certificate validation is never bypassed.' }
$base = [uri]($Url.AbsoluteUri.TrimEnd('/') + '/')
function Fetch([string]$Relative) {
    $address = [uri]::new($base, $Relative)
    $response = Invoke-WebRequest -Uri $address -UseBasicParsing -UseDefaultCredentials -MaximumRedirection 0
    if ($response.StatusCode -ne 200) { throw "Unexpected status for $Relative" }
    return $response
}
$index = Fetch 'index.html'
if ($index.Content -notmatch 'Enterprise XML Analyzer' -or $index.Headers['Content-Type'] -notmatch 'text/html') { throw 'Unexpected index.html response.' }
$manifest = (Fetch 'deployment-manifest.json').Content | ConvertFrom-Json
if ($manifest.appId -ne 'enterprise-xml-analyzer') { throw 'Unexpected application manifest.' }
$workers = 0
foreach ($entry in $manifest.files) {
    if ([IO.Path]::IsPathRooted($entry.path) -or $entry.path -match '(^|[/\\])\.\.([/\\]|$)' -or $entry.path -match '[:?#]') { throw 'Unsafe asset URL in manifest.' }
    if ($entry.path -eq 'web.config') { continue } # IIS intentionally does not serve web.config.
    $response = Fetch $entry.path
    if ($entry.path -match '\.js$' -and $response.Headers['Content-Type'] -notmatch '(text|application)/javascript') { throw "Incorrect JS MIME: $($entry.path)" }
    if ($entry.path -match '\.css$' -and $response.Headers['Content-Type'] -notmatch 'text/css') { throw "Incorrect CSS MIME: $($entry.path)" }
    if ($entry.path -match '\.worker-.*\.js$') { $workers++ }
}
if ($workers -lt 4) { throw 'Expected parser/index/diff/export worker assets were not found.' }
if ($index.Headers['Content-Security-Policy'] -notmatch "connect-src 'none'" -or $index.Headers['X-Content-Type-Options'] -ne 'nosniff') { throw 'Security headers are missing.' }
Write-Output "PASS: index, static files, MIME, $workers workers, security headers and $($Url.Scheme.ToUpper()) verified. Browser XML/XLSX tests remain required."
