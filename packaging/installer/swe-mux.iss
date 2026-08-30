; swe-mux Windows installer (Inno Setup 6).
;
; Compiled by `packaging/build_installer.py`, which is the only supported caller:
; every symbol below arrives on the ISCC command line, so compiling this file by
; hand fails loudly at "Undeclared identifier" rather than quietly producing an
; installer that names the wrong version or packs the wrong tree.
;
; Required symbols (all `/D` on the ISCC command line):
;   AppVersion      the display version, e.g. "0.1.0" or "0.2.0a1"
;   FileVersion     the four-part numeric version for the VERSIONINFO resource
;   AppSource       absolute path to the directory holding the three built bundles
;   SourceRoot      absolute path to the repository root (for LICENSE)
;   IconFile        absolute path to packaging/swe-mux.ico
; Optional:
;   SignTool        the name of an ISCC-registered sign tool (`/S<name>=...`).
;                   Absent means an unsigned build, which must always work.
;
; ---------------------------------------------------------------------------
; Four decisions in here are load-bearing and none is cosmetic.
;
; **The install is per-user and never elevates.** Every piece of state swe-mux
; owns is per-user already: the data directory is `%USERPROFILE%\.mux`, the login
; registration is `HKCU\...\Run`, the daemon binds loopback under the signed-in
; account, and `automation.secrets.json` holds current-user DPAPI blobs that no
; other account can read. A per-machine install would put the bundles
; somewhere a standard user cannot write, which is exactly the tree the staged
; swap renames during an upgrade - so it would trade an elevation prompt now for
; an update path that needs one every time. `PrivilegesRequired=lowest` with no
; override keeps one shape, which is also the only shape anything here is tested
; against.
;
; **The layout inside {app} is a contract with the running daemon, not a
; preference.** `supervisor_client.dedicated_supervisor_exe()` resolves the PTY
; supervisor as `<exe>\..\..\swe-mux-supervisor\swe-mux-supervisor.exe`, so the
; three bundles must be *siblings* under one parent, exactly as `dist/` has them:
;
;   {app}\swe-mux\swe-mux.exe
;   {app}\swe-mux-supervisor\swe-mux-supervisor.exe
;   {app}\swe-mux-cli\swemux.exe   (and mux.exe)
;
; Flattening them into {app} would resolve the supervisor one directory too high,
; and the daemon would silently fall back to `--supervisor-child` - which shares
; the app image and so re-creates the file-lock collision the separate bundle
; exists to prevent. `install_location.FROZEN_SIBLING_BUNDLES` reads the same
; layout from the other end, so `swemux doctor` run from the client describes the
; whole install rather than the one directory it happens to sit in.
;
; **The client bundle is what goes on PATH, and it is a third bundle for that
; reason** (ROADMAP Phase 23). `swe-mux.exe` is a GUI-subsystem executable with no
; stdout and no stderr at all, so publishing `{app}\swe-mux` under a name someone
; types expecting a session table would give them a window opener. And the client
; could not simply be added to the app bundle: a `swemux` sitting in a task
; terminal would hold `{app}\swe-mux` open against the `[InstallDelete]` below and
; against the in-app updater's staged swap, which is the same hazard
; `packaging/swe_mux.spec`'s `# No second executable` comment records. Its own
; directory means the only tree a running client can lock is its own.
;
; **The PATH edit is per-user, additive, and removes only its own entry.** It
; writes one directory into `HKCU\Environment\Path`, which needs no elevation and
; so matches the rest of this installer. Three properties it has to keep, each
; guarded below and each verified against Inno's own source rather than assumed:
;
;   1. *Idempotent.* `NeedsCliOnPath` refuses when the exact directory is already
;      present, so repeated installs and upgrades add nothing and the value does
;      not grow. Checked with delimiters on both ends, so `...\swe-mux-cli-old`
;      is not mistaken for a match.
;   2. *The existing value survives byte for byte.* Inno's `RegQueryStringValue`
;      returns REG_EXPAND_SZ data **unexpanded** (it is a plain `RegQueryValueEx`
;      with no `ExpandEnvironmentStrings`, `Shared.CommonFunc.pas`), and
;      `RegWriteStringValue` reads the existing type back and writes
;      REG_EXPAND_SZ when it finds one (`Setup.ScriptFunc.pas`). So a user whose
;      PATH contains `%USERPROFILE%\bin` keeps both the text and the type. The
;      `[Registry]` entry adds `preservestringtype` for the same reason on the
;      install side.
;   3. *Never silently truncated.* A composed value longer than
;      `MaxPathLength` is refused with a message instead of written, because a
;      PATH that was cut to fit is a machine whose other tools stopped resolving
;      and nothing said so.
;
; Removal is scoped by exact directory, like the login value: an uninstall strips
; only an entry naming *this* `{app}`, never a neighbouring one, and never a
; second swe-mux install's. The known gap is deliberate - if a user upgrades and
; *moves* the install directory, the old entry is orphaned rather than hunted
; down, because a greedy removal that eats an adjacent entry is worse than a
; stale one pointing at a directory that no longer exists.
;
; `ChangesEnvironment=yes` in [Setup] is what broadcasts WM_SETTINGCHANGE after
; install and uninstall; Inno sends it, so there is no hand-rolled
; `SendMessageTimeout` here. It reaches Explorer and anything started from it,
; and it cannot reach a console that is already open - which no mechanism can, so
; the task description and the Ready page say so in words instead.
;
; **Every comment in [Code] is `//`, never `{ ... }`.** Pascal's brace comment
; ends at the first `}`, and this script's comments are about `{app}` - so a
; braced one silently terminates mid-sentence and the rest of the prose is
; compiled as code. It fails at "'BEGIN' expected" pointing at the line *after*
; the comment, which is nowhere near the mistake. `tests/test_windows_installer.py`
; fails on a braced comment rather than leaving it for ISCC.
; ---------------------------------------------------------------------------

#ifndef AppVersion
  #error AppVersion must be defined; build through packaging/build_installer.py
#endif
#ifndef FileVersion
  #error FileVersion must be defined; build through packaging/build_installer.py
#endif
#ifndef AppSource
  #error AppSource must be defined; build through packaging/build_installer.py
#endif
#ifndef SourceRoot
  #error SourceRoot must be defined; build through packaging/build_installer.py
#endif
#ifndef IconFile
  #error IconFile must be defined; build through packaging/build_installer.py
#endif

; Never change this GUID. It is the identity Add/Remove Programs and every
; upgrade key off; a new one would leave the previous version installed beside
; this one with its own uninstall entry, which is precisely the orphaning an
; in-place upgrade has to avoid. Defined once and used twice - in [Setup] and in
; the uninstall-key lookup below - so the two can never name different products.
#define AppGuid "{7C4E1A64-2B5F-4E0B-9E2D-6E5B0D4A11C3}"

[Setup]
AppId={{#AppGuid}
AppName=swe-mux
AppVersion={#AppVersion}
VersionInfoVersion={#FileVersion}
VersionInfoProductVersion={#FileVersion}
VersionInfoProductName=swe-mux
VersionInfoDescription=swe-mux Setup
AppPublisher=swe-mux
AppPublisherURL=https://swemux.dev
AppSupportURL=https://github.com/jatoran/swe-mux/issues
AppUpdatesURL=https://github.com/jatoran/swe-mux/releases
LicenseFile={#SourceRoot}\LICENSE

; Per-user, no elevation. See the header for why this is not a default to relax.
PrivilegesRequired=lowest
; `{autopf}` under `lowest` is `%LOCALAPPDATA%\Programs`, the convention modern
; per-user Windows applications use.
DefaultDirName={autopf}\swe-mux
DefaultGroupName=swe-mux
DisableProgramGroupPage=yes
AllowNoIcons=yes

; x64-only, because the bundles PyInstaller produces are. Refusing to install on
; a host that cannot run them beats installing an app that fails at launch.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; The bundles are a few hundred megabytes of already-compressed payload; LZMA2
; with a large dictionary is what keeps the download reasonable.
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\swe-mux\swe-mux.exe
UninstallDisplayName=swe-mux {#AppVersion}

; Let Restart Manager find and close whatever is holding a file under {app}
; during an upgrade. Without it a running app locks its own `.exe` and the
; upgrade fails part-way, which is the worst of the available outcomes.
; `RestartApplications=no` because swe-mux is relaunched by the [Run] entry (or
; by the operator) rather than by Setup restoring a process it killed.
CloseApplications=yes
RestartApplications=no

; Broadcast WM_SETTINGCHANGE after installing and after uninstalling, because
; this script writes `HKCU\Environment\Path`. Without it, Explorer - and
; therefore every terminal launched from it afterwards - keeps the environment
; block it started with, and the new command appears only after a sign-out.
ChangesEnvironment=yes

#ifdef SignTool
; Only emitted when the caller registered a sign tool. An unsigned build must
; compile and publish cleanly, so there is deliberately no default here and no
; step that fails when this is absent.
SignTool={#SignTool}
SignedUninstaller=yes
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
Name: "startupicon"; Description: "Start swe-mux when I &sign in (runs hidden in the system tray)"; GroupDescription: "Startup:"; Flags: unchecked
; Ticked by default, unlike the two above, and the difference is deliberate.
; Those create artifacts the user did not ask for - an icon on their desktop, a
; process at every sign-in - so the polite default is off. This one only makes an
; already-installed command answer to its own name, which is what "installed"
; means for every other way of getting swe-mux: `pip`, `pipx` and `uv tool` all
; put `swemux` and `mux` on PATH, and the whole point of this entry is that an
; installer user ends up with the same commands (ROADMAP Phase 23 exit criteria).
; It is a task rather than an unconditional write so that a user who curates
; their PATH by hand can say no.
Name: "addtopath"; Description: "Add the &swemux and mux commands to my PATH (open a new terminal afterwards)"; GroupDescription: "Command line:"

[Files]
; All three bundles, each into its own sibling directory. `recursesubdirs` +
; `createallsubdirs` carries `_internal/` whole, including the empty directories
; PyInstaller's collected packages sometimes leave behind.
Source: "{#AppSource}\swe-mux\*"; DestDir: "{app}\swe-mux"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#AppSource}\swe-mux-supervisor\*"; DestDir: "{app}\swe-mux-supervisor"; Flags: ignoreversion recursesubdirs createallsubdirs
; Installed whether or not the PATH task was ticked: the commands exist either
; way and can be run by full path, and `swemux doctor` says where they are. The
; task governs reachability, not presence.
Source: "{#AppSource}\swe-mux-cli\*"; DestDir: "{app}\swe-mux-cli"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#IconFile}"; DestDir: "{app}"; DestName: "swe-mux.ico"; Flags: ignoreversion

[InstallDelete]
; Remove the previous bundles before writing the new ones. A PyInstaller onedir
; tree is not additive: a dependency dropped between releases leaves its old
; `.pyd`/`.dll` behind, and Python will happily import the stale one. Copying
; over the top produces a tree that is neither version, and the failure appears
; at runtime rather than here.
Type: filesandordirs; Name: "{app}\swe-mux"
Type: filesandordirs; Name: "{app}\swe-mux-supervisor"
Type: filesandordirs; Name: "{app}\swe-mux-cli"

[Icons]
Name: "{group}\swe-mux"; Filename: "{app}\swe-mux\swe-mux.exe"; IconFilename: "{app}\swe-mux.ico"; Comment: "swe-mux workspace"
Name: "{autodesktop}\swe-mux"; Filename: "{app}\swe-mux\swe-mux.exe"; IconFilename: "{app}\swe-mux.ico"; Tasks: desktopicon

[Registry]
; The **one** writer of the login registration in this installer, and it writes
; the same key and the same value name the tray's own "Start with Windows"
; toggle owns (`desktop.RUN_KEY` / `desktop.RUN_VALUE`). One registration with
; one name is what keeps the two surfaces describing the same fact; a second
; mechanism - a Startup-folder shortcut, say - would autostart the app while the
; tray's checkbox reported that nothing did.
;
; Removal is deliberately NOT `uninsdeletevalue`: the uninstaller deletes this
; value only when it still points inside the directory being removed
; (`CurUninstallStepChanged` below), so uninstalling one install can never strip
; the login entry of a different one.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "swe-mux"; ValueData: "{code:StartupCommand}"; Tasks: startupicon

; The user PATH, so `swemux` and `mux` answer by name. Per-user because the whole
; install is (`PrivilegesRequired=lowest`), and a per-user PATH edit needs no
; elevation either.
;
; `Check: NeedsCliOnPath` is what makes this idempotent: it returns False when the
; directory is already there, the entry is skipped, and an upgrade neither
; duplicates nor grows the value. `preservestringtype` keeps a REG_EXPAND_SZ Path
; expandable; `expandsz` is only used when the value has to be created. The data
; is composed in code rather than with `{olddata}` so the no-existing-Path case
; produces `C:\...\swe-mux-cli` rather than a value with a leading separator.
; `PathWithCliDir` refuses instead of truncating when the result would be too
; long, and returns the value unchanged in that case, which leaves this write a
; no-op.
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{code:PathWithCliDir}"; Flags: preservestringtype; Tasks: addtopath; Check: NeedsCliOnPath

[Run]
Filename: "{app}\swe-mux\swe-mux.exe"; Description: "Launch swe-mux"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The bundles are removed by the uninstall log; these cover anything created
; beside them after install (a staged or rolled-back tree, a log written into the
; bundle root) so an uninstall does not leave a half-empty directory behind.
Type: filesandordirs; Name: "{app}\swe-mux"
Type: filesandordirs; Name: "{app}\swe-mux-supervisor"
Type: filesandordirs; Name: "{app}\swe-mux-cli"
Type: dirifempty; Name: "{app}"

[Code]

// The user's PATH lives here, and this is the only key this script edits for it.
const
  EnvironmentKey = 'Environment';
  PathValueName = 'Path';
  // The Windows environment block is limited to 32767 characters, so a PATH
  // longer than that cannot be delivered to a process even when the registry
  // accepts it. Refuse at the boundary rather than write a value the system will
  // truncate for us - a silently shortened PATH is other people's tools failing
  // to resolve, days later, with nothing pointing here.
  MaxPathLength = 32767;

// The directory holding `swemux.exe` and `mux.exe`; the one thing put on PATH.
//
// A function rather than a constant because `{app}` is not known until the user
// has chosen it, and every caller here needs the resolved form to compare
// against what is already in the value.
function CliDir(): String;
begin
  Result := ExpandConstant('{app}\swe-mux-cli');
end;

// The user's PATH exactly as stored, or '' when the value does not exist.
//
// `RegQueryStringValue` accepts REG_SZ and REG_EXPAND_SZ and returns the raw
// data for both - it is a `RegQueryValueEx` with no expansion step - so a value
// containing `%USERPROFILE%` comes back with the variable intact and can be
// written straight back without losing it.
function CurrentUserPath(): String;
begin
  if not RegQueryStringValue(HKCU, EnvironmentKey, PathValueName, Result) then
    Result := '';
end;

// Whether `Dir` is already one of the entries in `Paths`.
//
// Delimited on both ends before searching, so a value ending in the directory
// matches and a longer neighbour that merely starts with it does not:
// `...\swe-mux-cli-old` must not read as `...\swe-mux-cli`. Case-insensitive,
// because Windows paths are.
function PathContainsDir(const Paths: String; const Dir: String): Boolean;
begin
  Result := Pos(';' + Lowercase(Dir) + ';', ';' + Lowercase(Paths) + ';') > 0;
end;

// The [Registry] Check: True only when the entry is genuinely missing.
//
// This is what makes repeated installs and every upgrade a no-op on PATH: Inno
// skips the entry entirely when a Check returns False, so nothing is written,
// nothing is duplicated, and the value cannot grow by one directory per release.
function NeedsCliOnPath(): Boolean;
begin
  Result := not PathContainsDir(CurrentUserPath(), CliDir());
end;

// The value to write: the existing PATH with our directory appended.
//
// Refuses rather than truncates. Returning the current value unchanged when the
// result would overflow makes the [Registry] write a no-op, which is the safe
// outcome. It is always logged and only shown as a dialog when a person is
// there: `/VERYSILENT` is a supported way to install this, and a MsgBox in that
// mode is an unattended install that hangs forever waiting for a click.
function PathWithCliDir(Param: String): String;
var
  Existing: String;
  Candidate: String;
  Complaint: String;
begin
  Existing := CurrentUserPath();
  if Existing = '' then
    Candidate := CliDir()
  else
    Candidate := Existing + ';' + CliDir();
  if Length(Candidate) > MaxPathLength then begin
    Complaint := 'swe-mux did not change your PATH: adding ' + CliDir()
      + ' would make it longer than Windows can pass to a program ('
      + IntToStr(MaxPathLength) + ' characters), and shortening it would break'
      + ' other tools.' + #13#10 + #13#10
      + 'The commands are still installed. Run them by full path, or shorten your'
      + ' PATH and re-run this installer.';
    Log(Complaint);
    if not WizardSilent then
      MsgBox(Complaint, mbError, MB_OK);
    Result := Existing;
    Exit;
  end;
  Result := Candidate;
end;

// Take our directory back out of PATH, leaving every other entry as it was.
//
// Rebuilt entry by entry rather than by cutting a substring, so a separator is
// never left doubled and an entry that merely contains our path as a prefix is
// never touched. `RegWriteStringValue` preserves an existing REG_EXPAND_SZ type
// (it queries the type first), so a PATH with `%USERPROFILE%` in it survives
// this as REG_EXPAND_SZ with its text intact.
//
// Called only from the uninstaller, and only for the directory under the `{app}`
// being removed - so a second swe-mux install, or a PATH entry a user added by
// hand for something else, is not this uninstaller's to delete.
// The `First` flag rather than a `Rebuilt = ''` test is what keeps an *empty*
// entry - a leading or trailing `;`, which PATH reads as the current directory -
// exactly where the user had it. Testing the accumulator instead would silently
// drop it and quietly change the meaning of somebody else's PATH, which is the
// class of damage this whole procedure exists to avoid.
procedure RemoveCliDirFromPath;
var
  Existing: String;
  Rebuilt: String;
  Entry: String;
  Dir: String;
  Rest: String;
  Cut: Integer;
  First: Boolean;
  Remaining: Boolean;
begin
  Existing := CurrentUserPath();
  Dir := Lowercase(CliDir());
  if not PathContainsDir(Existing, CliDir()) then
    Exit;
  Rebuilt := '';
  Rest := Existing;
  First := True;
  Remaining := True;
  while Remaining do begin
    Cut := Pos(';', Rest);
    if Cut = 0 then begin
      Entry := Rest;
      Rest := '';
      Remaining := False;
    end else begin
      Entry := Copy(Rest, 1, Cut - 1);
      Rest := Copy(Rest, Cut + 1, Length(Rest) - Cut);
    end;
    if Lowercase(Entry) <> Dir then begin
      if First then begin
        Rebuilt := Entry;
        First := False;
      end else
        Rebuilt := Rebuilt + ';' + Entry;
    end;
  end;
  RegWriteStringValue(HKCU, EnvironmentKey, PathValueName, Rebuilt);
end;

// Reproduce `subprocess.list2cmdline`'s quoting for one argument.
//
// The tray decides whether "Start with Windows" is on by comparing the registry
// value against `desktop.startup_command()` *exactly*, so a value that differs by
// one pair of quotes reads as off. list2cmdline quotes an argument iff it is
// empty or contains a space, tab, newline, vertical tab, or a double quote; a
// Windows file path can contain none of those but a space or a tab, so this is a
// faithful reproduction over the domain it is applied to.
function QuoteArg(const S: String): String;
begin
  if (Pos(' ', S) > 0) or (Pos(#9, S) > 0) then
    Result := '"' + S + '"'
  else
    Result := S;
end;

// The home directory `pathlib.Path.home()` resolves to on Windows.
//
// Read from the environment rather than from an Inno constant, because Inno has
// no `{userprofile}` (a compile accepts it and the *install* dies at "Unknown
// constant") and because `Path.home()` reads exactly these variables in exactly
// this order - `USERPROFILE`, then `HOMEDRIVE` + `HOMEPATH`. Reproducing the
// source of the value is what makes the two agree, rather than reproducing one
// machine's answer.
function HomeDir(): String;
begin
  Result := Trim(GetEnv('USERPROFILE'));
  if Result = '' then
    Result := Trim(GetEnv('HOMEDRIVE')) + Trim(GetEnv('HOMEPATH'));
end;

// Where `config.default_data_dir()` puts `config.toml` on this machine.
//
// `MUX_DATA_DIR` first, then `<home>\.mux`, which is what that function does on
// Windows - the POSIX conventions below it are unreachable here. A
// `MUX_DATA_DIR` written with a leading `~` is taken literally rather than
// expanded, which is the one case where this can disagree with the app; the cost
// of that disagreement is a tray checkbox that reads off until it is clicked
// once, never a missing or duplicated registration.
function ConfigPath(): String;
var
  Base: String;
begin
  Base := Trim(GetEnv('MUX_DATA_DIR'));
  if Base = '' then
    Base := AddBackslash(HomeDir()) + '.mux';
  Result := AddBackslash(Base) + 'config.toml';
end;

// The exact string `desktop.startup_command()` writes for a frozen build:
// `<exe> --hidden --config <config.toml>`, each part quoted as list2cmdline
// would. Argument order is part of the reproduction and is asserted against the
// Python side by `tests/test_windows_installer.py`.
function StartupCommand(Param: String): String;
begin
  Result := QuoteArg(ExpandConstant('{app}\swe-mux\swe-mux.exe'))
    + ' --hidden --config '
    + QuoteArg(ConfigPath());
end;

// The version already installed under this AppId, or '' when this is a fresh
// install. Read from the per-user uninstall key, which is where a
// `PrivilegesRequired=lowest` install registers.
function InstalledVersion(): String;
begin
  if not RegQueryStringValue(HKCU,
      'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#AppGuid}_is1',
      'DisplayVersion', Result) then
    Result := '';
end;

// Say what an upgrade costs before it is started, not after.
//
// Replacing the bundles means closing the app *and* the PTY supervisor, and
// stopping the supervisor ends every live terminal session - the deliberate,
// out-of-band act the project's own supervisor-update flow exists to make
// explicit. Restart Manager will offer to close them a page later; this is the
// consequence that offer does not state. Appended to the Ready page rather than
// shown as its own dialog, so a fresh install (which has nothing to reap) is not
// interrupted by a warning about sessions that do not exist.
function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo,
  MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
var
  Previous: String;
begin
  Result := MemoDirInfo + NewLine + NewLine + MemoGroupInfo;
  if MemoTasksInfo <> '' then
    Result := Result + NewLine + NewLine + MemoTasksInfo;
  // Said here because no mechanism can fix it: `ChangesEnvironment=yes` broadcasts
  // WM_SETTINGCHANGE, which reaches Explorer and everything started from it after
  // that, and cannot reach a console window that is already open. Somebody who
  // ticked the PATH task and then tried it in the terminal they already had would
  // otherwise conclude the task did nothing.
  if WizardIsTaskSelected('addtopath') then
    Result := Result + NewLine + NewLine
      + 'Command line:' + NewLine
      + Space + 'swemux and mux are added to your PATH. Terminals that are' + NewLine
      + Space + 'already open keep the old PATH, so open a new one.';
  Previous := InstalledVersion();
  if Previous <> '' then
    Result := Result + NewLine + NewLine
      + 'Upgrading from ' + Previous + ':' + NewLine
      + Space + 'Setup replaces the swe-mux application and its PTY supervisor.' + NewLine
      + Space + 'Closing the supervisor ends every live terminal session, so' + NewLine
      + Space + 'finish or detach any running agents before continuing.';
end;

// Remove the login registration on uninstall, but only our own.
//
// Scoped by target rather than by whether the task was ticked: the tray can turn
// "Start with Windows" on after install, and an uninstall that left that value
// behind would point Windows at an executable that no longer exists on every
// sign-in. Equally, a value naming some other swe-mux - a source checkout, a
// second install - is not this uninstaller's to delete, which is why the string
// has to name the install directory before it goes.
procedure CurUninstallStepChanged(CurStep: TUninstallStep);
var
  Existing: String;
  Root: String;
begin
  if CurStep <> usUninstall then
    Exit;
  // The PATH entry first, and unconditionally: it is scoped by exact directory,
  // so an install whose task was never ticked simply finds nothing to remove.
  // Doing it here rather than from a [Registry] flag is the only way to take one
  // entry out of a shared value; `uninsdeletevalue` on `Path` would delete the
  // user's entire PATH.
  RemoveCliDirFromPath;
  if not RegQueryStringValue(HKCU,
      'Software\Microsoft\Windows\CurrentVersion\Run', 'swe-mux', Existing) then
    Exit;
  Root := ExpandConstant('{app}');
  if Pos(Lowercase(Root), Lowercase(Existing)) > 0 then
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'swe-mux');
end;
