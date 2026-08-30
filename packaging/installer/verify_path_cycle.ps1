<#
.SYNOPSIS
  Prove the swe-mux installer's PATH handling by running the whole cycle.

.DESCRIPTION
  Unit tests cannot prove an installer. `tests/test_windows_installer.py` reads the
  `.iss` as text because the suite cannot run ISCC, and every assertion there is
  about a string being present - which is worth having and is not evidence that
  the PATH edit works. This is the evidence: install, upgrade over the top,
  uninstall, and compare `HKCU\Environment\Path` byte for byte at each step.

  It answers four questions that only a real run can:

    1. After install, PATH gained **exactly one** entry and it is the client
       bundle's directory.
    2. `swemux` and `mux` resolve by name in a process that reads the new
       environment, and actually run - including exiting 3 against an unreachable
       daemon, which is the documented code a script would branch on.
    3. Installing again over the top changes nothing - no duplicate, no growth.
       This is the upgrade case, and it is where a naive appender fails.
    4. After uninstall, PATH is **identical** to what it was before - same text,
       same registry value kind. A REG_EXPAND_SZ value holding `%USERPROFILE%\bin`
       must come back with the variable intact, not flattened to this machine's
       answer.

  The value kind is checked because it is the failure nobody notices: writing a
  REG_EXPAND_SZ PATH back as REG_SZ leaves every `%VAR%` entry in it as literal
  text, so the affected directories silently stop resolving for every program on
  the machine.

.NOTES
  **This edits the current user's PATH**, which is why it refuses to start unless
  `SWE_MUX_INSTALLER_CYCLE=1` is set. Intended for an ephemeral CI runner. It
  saves the original value and kind up front and restores them in a `finally`.

  Three things make it survivable on a machine somebody cares about, which is
  where it was first run for real (2026-08-30, Inno 6.7.3):

  - **The original PATH is written to a file before the seed**, and deleted only
    after a successful restore. A `finally` does not run if the process is killed,
    so without the file the recovery story is "remember 1337 characters". The path
    of that file is printed, and the run ends by telling you it is gone.
  - **It refuses if the AppId is already registered.** Add/Remove Programs is keyed
    by a fixed `AppId` that no command-line switch can override, so running setup
    against an existing install is an *upgrade of that install* - and the uninstall
    at the end would then deregister somebody's real copy.
  - **`/GROUP=` redirects the Start Menu folder.** `[Icons]` writes `{group}\swe-mux`
    unconditionally, with no task gating it, so a default-group run would overwrite
    a real shortcut and the uninstall would delete it.

.PARAMETER Installer
  The compiled `*-setup.exe` to exercise.

.PARAMETER InstallDir
  Where to install it. Passed as `/DIR=`, so it never lands in the default
  location and cannot collide with a real install.

.PARAMETER Group
  The Start Menu folder, passed as `/GROUP=`. Defaulted to a name no real install
  uses for the reason above.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Installer,
  [Parameter(Mandatory = $true)][string]$InstallDir,
  [string]$Group = 'swe-mux PATH cycle (test)'
)

$ErrorActionPreference = 'Stop'

# A non-zero exit from a native command is data here, not an error: this script's
# whole point is asserting on exit codes, and `swemux ls` against a dead daemon is
# *supposed* to exit 3. PowerShell 7.4 turns `$PSNativeCommandUseErrorActionPreference`
# on by default, which combined with `Stop` above would turn that expected 3 into a
# terminating error before the assertion ever ran. Guarded because the variable does
# not exist on Windows PowerShell 5.1, which is also a host this may run on.
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
  $PSNativeCommandUseErrorActionPreference = $false
}

if ($env:SWE_MUX_INSTALLER_CYCLE -ne '1') {
  throw "This rewrites HKCU\Environment\Path. Set SWE_MUX_INSTALLER_CYCLE=1 to confirm you are on a machine where that is acceptable (an ephemeral CI runner)."
}

$EnvKey = 'HKCU:\Environment'
# `RUNNER_TEMP` on a GitHub runner, the OS temp directory anywhere else, so this
# is runnable by hand on a throwaway machine and not only in CI.
$Scratch = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [System.IO.Path]::GetTempPath() }
$Recovery = Join-Path $Scratch 'swe-mux-path-cycle-recovery.json'

# Never changeable from the command line, so a collision here is not something the
# caller can route around - see the AppId note in the .NOTES block and the comment
# above `#define AppGuid` in swe-mux.iss.
$AppGuid = '{7C4E1A64-2B5F-4E0B-9E2D-6E5B0D4A11C3}'
foreach ($hive in @('HKCU:', 'HKLM:')) {
  $key = "$hive\Software\Microsoft\Windows\CurrentVersion\Uninstall\${AppGuid}_is1"
  if (Test-Path $key) {
    throw ("swe-mux is already installed and registered at $key. This cycle would " +
           "be treated as an upgrade of it, and the uninstall at the end would " +
           "deregister it. Run this where swe-mux is not installed.")
  }
}

function Get-UserPath {
  # Read the *raw* value, exactly as the installer's Pascal does.
  # `DoNotExpandEnvironmentNames` is what keeps `%USERPROFILE%` a variable rather
  # than this machine's answer for it; `[Environment]::GetEnvironmentVariable(
  # 'Path', 'User')` expands, and comparing expanded values would prove nothing
  # about whether the variable reference survived.
  $key = Get-Item -Path $EnvKey
  $raw = $key.GetValue('Path', $null, 'DoNotExpandEnvironmentNames')
  if ($null -eq $raw) { return $null }
  return [string]$raw
}

function Get-UserPathKind {
  $key = Get-Item -Path $EnvKey
  if ($null -eq $key.GetValue('Path', $null, 'DoNotExpandEnvironmentNames')) { return $null }
  return $key.GetValueKind('Path').ToString()
}

# Deliberately untyped parameters. A `[string]` parameter coerces `$null` to the
# empty string, so a machine whose `HKCU\Environment\Path` did not exist before
# this ran would have one *created* by the restore rather than left absent - which
# is the one thing a restore must not do.
function Set-UserPath($Value, $Kind) {
  if ($null -eq $Value) {
    Remove-ItemProperty -Path $EnvKey -Name 'Path' -ErrorAction SilentlyContinue
    return
  }
  $type = if ($Kind -eq 'String') { 'String' } else { 'ExpandString' }
  Set-ItemProperty -Path $EnvKey -Name 'Path' -Value ([string]$Value) -Type $type
}

function Split-PathValue([string]$Value) {
  if ([string]::IsNullOrEmpty($Value)) { return @() }
  return $Value.Split(';')
}

function Assert([bool]$Condition, [string]$Message) {
  if (-not $Condition) { throw "FAILED: $Message" }
  Write-Host "  ok  $Message"
}

# Wait for a condition, because `Start-Process -Wait` is not enough for the
# uninstaller.
#
# Inno's `unins000.exe` copies itself into `%TEMP%` and re-launches, then the
# first process exits - so `-Wait` returns while the uninstall is still running,
# and every assertion after it would race a PATH edit that has not happened yet.
# Polling the observable end state is what makes the sequence deterministic; a
# fixed sleep would be either flaky or slow, and on a shared runner usually both.
function Wait-Until([scriptblock]$Condition, [string]$What, [int]$TimeoutSeconds = 120) {
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    if (& $Condition) { return }
    Start-Sleep -Milliseconds 500
  }
  throw "FAILED: timed out after ${TimeoutSeconds}s waiting for $What"
}

$originalValue = Get-UserPath
$originalKind = Get-UserPathKind
$cliDir = Join-Path $InstallDir 'swe-mux-cli'

# Written before the seed, not after: the `finally` below does not run if this
# process is killed, and the value it would have restored is the only copy.
@{ kind = $originalKind; value = $originalValue } | ConvertTo-Json -Depth 3 |
  Set-Content -Path $Recovery -Encoding UTF8
Write-Host "PATH before this run is saved at $Recovery"
Write-Host "  restore by hand with: Set-ItemProperty 'HKCU:\Environment' Path <value> -Type $originalKind"

try {
  # A seeded PATH that exercises the property most likely to be damaged: an
  # unexpanded variable reference, stored as REG_EXPAND_SZ. If the installer read
  # this expanded and wrote it back, `%USERPROFILE%\bin` would come out as
  # `C:\Users\runneradmin\bin` and this run would say so.
  $seed = '%USERPROFILE%\bin;C:\Windows\system32;C:\tools\swe-mux-cli-old'
  Set-UserPath $seed 'ExpandString'
  $before = Get-UserPath
  Assert ($before -eq $seed) "the seeded PATH reads back unexpanded"
  Assert ((Get-UserPathKind) -eq 'ExpandString') "the seeded PATH is REG_EXPAND_SZ"

  Write-Host "`n== install =="
  # `/TASKS=addtopath` *replaces* the default selection rather than adding to it,
  # so this run also proves the two shortcut tasks stay off when they are not
  # named. The upgrade below omits the flag entirely, which takes the defaults -
  # so between them the two runs cover both routes to selecting this task.
  $log = Join-Path $Scratch 'swe-mux-install.log'
  $arguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                 "/DIR=$InstallDir", "/GROUP=$Group", '/TASKS=addtopath', "/LOG=$log")
  $process = Start-Process -FilePath $Installer -ArgumentList $arguments -Wait -PassThru
  Assert ($process.ExitCode -eq 0) "setup exited 0 (was $($process.ExitCode))"

  $after = Get-UserPath
  Write-Host "  before: $before"
  Write-Host "  after:  $after"
  $added = @(Compare-Object (Split-PathValue $before) (Split-PathValue $after) |
             Where-Object { $_.SideIndicator -eq '=>' } |
             ForEach-Object { $_.InputObject })
  Assert ($added.Count -eq 1) "exactly one PATH entry was added (got $($added.Count): $($added -join ', '))"
  Assert ($added[0] -eq $cliDir) "the added entry is $cliDir (got $($added[0]))"
  Assert ($after.StartsWith($seed)) "every pre-existing entry survived, in order and unmodified"
  Assert ($after.Contains('%USERPROFILE%\bin')) "the unexpanded variable reference survived"
  Assert ((Get-UserPathKind) -eq 'ExpandString') "the value is still REG_EXPAND_SZ"
  # The prefix-collision case, which a substring search would have eaten.
  Assert ($after.Contains('C:\tools\swe-mux-cli-old')) "a neighbouring entry that merely starts the same was untouched"

  Write-Host "`n== the command resolves and runs by name =="
  # A *new* environment, assembled the way a freshly launched process gets one,
  # rather than this session's stale copy. `ChangesEnvironment=yes` broadcasts
  # WM_SETTINGCHANGE so Explorer-launched shells pick this up; a process already
  # running (this one) never does, which is the thing the installer's own text
  # tells the user.
  #
  # Resolution is asked of `where.exe`, and execution goes through `Start-Process`
  # with a bare name - both resolve against the environment as a freshly launched
  # process sees it. PowerShell's own `Get-Command` caches command discovery per
  # session, so asking it after mutating `$env:Path` in the same session is the one
  # way to get a confidently wrong answer here.
  $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
              [Environment]::GetEnvironmentVariable('Path', 'User')
  # A throwaway data directory, so a run on a real machine cannot touch `~/.mux`.
  $env:MUX_DATA_DIR = Join-Path $Scratch 'swe-mux-cycle-data'

  $resolved = @(& where.exe swemux 2>$null)
  Assert ($resolved.Count -ge 1 -and $resolved[0] -eq (Join-Path $cliDir 'swemux.exe')) `
    "swemux resolves to $cliDir\swemux.exe (got '$($resolved -join ', ')')"
  $aliasResolved = @(& where.exe mux 2>$null)
  Assert ($aliasResolved.Count -ge 1 -and $aliasResolved[0] -eq (Join-Path $cliDir 'mux.exe')) `
    "mux resolves to $cliDir\mux.exe (got '$($aliasResolved -join ', ')')"

  # And it is a real program rather than a file with the right name. The second
  # call is the exit-code contract: `swe_mux.cli.main` *returns* its code, so a
  # frozen entry point that called it bare would print the right error and exit 0
  # (measured, and what `packaging/cli_entry.py` exists to prevent). Port 1 is
  # never listening, and nothing here binds anything.
  $help = Start-Process -FilePath 'swemux' -ArgumentList '--help' -Wait -PassThru -NoNewWindow
  Assert ($help.ExitCode -eq 0) "swemux --help exited 0 when run by name (was $($help.ExitCode))"
  $dead = Start-Process -FilePath 'swemux' `
    -ArgumentList @('ls', '--url', 'http://127.0.0.1:1') -Wait -PassThru -NoNewWindow
  Assert ($dead.ExitCode -eq 3) "an unreachable daemon exits 3, the documented code (was $($dead.ExitCode))"

  Write-Host "`n== upgrade over the top =="
  # No `/TASKS` this time: the default selection, which is what a user who takes
  # the wizard's defaults gets and what the task being ticked-by-default means.
  $upgradeArguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                        "/DIR=$InstallDir", "/GROUP=$Group", "/LOG=$log")
  $process = Start-Process -FilePath $Installer -ArgumentList $upgradeArguments -Wait -PassThru
  Assert ($process.ExitCode -eq 0) "the second setup exited 0 (was $($process.ExitCode))"
  $upgraded = Get-UserPath
  Assert ($upgraded -eq $after) "PATH is byte-identical after installing over the top"
  $occurrences = @(Split-PathValue $upgraded | Where-Object { $_ -eq $cliDir })
  Assert ($occurrences.Count -eq 1) "exactly one $cliDir entry (got $($occurrences.Count))"

  Write-Host "`n== uninstall =="
  $uninstaller = Get-ChildItem -Path $InstallDir -Filter 'unins*.exe' | Select-Object -First 1
  Assert ($null -ne $uninstaller) "the uninstaller is present in $InstallDir"
  $process = Start-Process -FilePath $uninstaller.FullName -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') -Wait -PassThru
  Assert ($process.ExitCode -eq 0) "uninstall exited 0 (was $($process.ExitCode))"
  Wait-Until { -not (Test-Path (Join-Path $cliDir 'swemux.exe')) } "the client bundle to be removed"
  # The PATH edit happens at `usUninstall`, so the launcher going away is not by
  # itself proof that it has been made. Waited on as "the value changed at all"
  # rather than "the value is right", so the assertion below still has something
  # to decide and a wrong edit fails as a wrong edit instead of as a timeout.
  Wait-Until { (Get-UserPath) -ne $after } "the uninstaller's PATH edit"

  $restored = Get-UserPath
  Write-Host "  after uninstall: $restored"
  Assert ($restored -eq $before) "PATH is byte-identical to what it was before install"
  Assert ((Get-UserPathKind) -eq 'ExpandString') "the value kind is unchanged by the uninstall"
  Assert (-not (Test-Path (Join-Path $InstallDir 'swe-mux-cli'))) "the client bundle was removed"

  # The Start Menu folder is redirected by `/GROUP=`, so an orphan here would be
  # named after this script rather than after the product - but an orphan is still
  # an orphan, and the uninstall is what this run is about.
  $group = Join-Path $env:APPDATA (Join-Path 'Microsoft\Windows\Start Menu\Programs' $Group)
  Assert (-not (Test-Path $group)) "the Start Menu folder was removed ($group)"

  Write-Host "`nPATH cycle verified."
}
finally {
  Set-UserPath $originalValue $originalKind
  if ((Get-UserPath) -eq $originalValue -and (Get-UserPathKind) -eq $originalKind) {
    Remove-Item -Path $Recovery -ErrorAction SilentlyContinue
    Write-Host "PATH restored; recovery file removed."
  } else {
    Write-Warning "PATH was NOT restored to its original value. It is in $Recovery."
  }
}
