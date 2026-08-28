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
;   AppSource       absolute path to the directory holding the two built bundles
;   SourceRoot      absolute path to the repository root (for LICENSE)
;   IconFile        absolute path to packaging/swe-mux.ico
; Optional:
;   SignTool        the name of an ISCC-registered sign tool (`/S<name>=...`).
;                   Absent means an unsigned build, which must always work.
;
; ---------------------------------------------------------------------------
; Two decisions in here are load-bearing and neither is cosmetic.
;
; **The install is per-user and never elevates.** Every piece of state swe-mux
; owns is per-user already: the data directory is `%USERPROFILE%\.mux`, the login
; registration is `HKCU\...\Run`, the daemon binds loopback under the signed-in
; account, and `automation.secrets.json` holds current-user DPAPI blobs that no
; other account can read. A per-machine install would put the two bundles
; somewhere a standard user cannot write, which is exactly the tree the staged
; swap renames during an upgrade - so it would trade an elevation prompt now for
; an update path that needs one every time. `PrivilegesRequired=lowest` with no
; override keeps one shape, which is also the only shape anything here is tested
; against.
;
; **The layout inside {app} is a contract with the running daemon, not a
; preference.** `supervisor_client.dedicated_supervisor_exe()` resolves the PTY
; supervisor as `<exe>\..\..\swe-mux-supervisor\swe-mux-supervisor.exe`, so the
; two bundles must be *siblings* under one parent, exactly as `dist/` has them:
;
;   {app}\swe-mux\swe-mux.exe
;   {app}\swe-mux-supervisor\swe-mux-supervisor.exe
;
; Flattening them into {app} would resolve the supervisor one directory too high,
; and the daemon would silently fall back to `--supervisor-child` - which shares
; the app image and so re-creates the file-lock collision the separate bundle
; exists to prevent.
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

[Files]
; Both bundles, each into its own sibling directory. `recursesubdirs` +
; `createallsubdirs` carries `_internal/` whole, including the empty directories
; PyInstaller's collected packages sometimes leave behind.
Source: "{#AppSource}\swe-mux\*"; DestDir: "{app}\swe-mux"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#AppSource}\swe-mux-supervisor\*"; DestDir: "{app}\swe-mux-supervisor"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#IconFile}"; DestDir: "{app}"; DestName: "swe-mux.ico"; Flags: ignoreversion

[InstallDelete]
; Remove the previous bundles before writing the new ones. A PyInstaller onedir
; tree is not additive: a dependency dropped between releases leaves its old
; `.pyd`/`.dll` behind, and Python will happily import the stale one. Copying
; over the top produces a tree that is neither version, and the failure appears
; at runtime rather than here.
Type: filesandordirs; Name: "{app}\swe-mux"
Type: filesandordirs; Name: "{app}\swe-mux-supervisor"

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

[Run]
Filename: "{app}\swe-mux\swe-mux.exe"; Description: "Launch swe-mux"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The bundles are removed by the uninstall log; these cover anything created
; beside them after install (a staged or rolled-back tree, a log written into the
; bundle root) so an uninstall does not leave a half-empty directory behind.
Type: filesandordirs; Name: "{app}\swe-mux"
Type: filesandordirs; Name: "{app}\swe-mux-supervisor"
Type: dirifempty; Name: "{app}"

[Code]

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
  if not RegQueryStringValue(HKCU,
      'Software\Microsoft\Windows\CurrentVersion\Run', 'swe-mux', Existing) then
    Exit;
  Root := ExpandConstant('{app}');
  if Pos(Lowercase(Root), Lowercase(Existing)) > 0 then
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'swe-mux');
end;
