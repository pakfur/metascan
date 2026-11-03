; The Metascan Windows installer. Build it with installer\build.ps1, which stages
; the files this script packs; see docs/windows-installer.md.
;
; Setup copies the app and asks its questions. The long part (Python, torch,
; models) is installer\provision.ps1, run after the copy, with its progress
; shown here. Metascan writes data, logs and config.json next to its code, so it
; installs per user, where it can write, and needs no administrator.
;
; Unattended: /VERYSILENT /SUPPRESSMSGBOXES /DIR=<folder> plus any of
;   /LIBRARY=<folder;folder>  /COMFYHOST=<host>  /COMFYPORT=<port>
;   /ORIGINAL=<folder>  /NOPREREQS  /NOCLIP  /CPUTORCH

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef Payload
  #define Payload "build\payload"
#endif
#ifndef Assets
  #define Assets "build\assets"
#endif

#define AppName "Metascan"
#define LauncherPy '""{app}\installer\launcher.py""'
#define WingetFolder "{localappdata}\Microsoft\WindowsApps"

[Setup]
AppId={{7D3C2F4E-8A61-4B0F-9C57-1E6B0A7C3D21}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Metascan
AppPublisherURL=https://github.com/pakfur/metascan
AppSupportURL=https://github.com/pakfur/metascan/issues
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no
DisableWelcomePage=no
DisableReadyPage=no
PrivilegesRequired=lowest
; On, Setup's children could not follow a symlink a non-admin made. That is every
; Hugging Face cache entry, so CLIP would fail to load, here and in a Metascan
; started from the last page.
RedirectionGuard=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
; Python, CUDA torch and the largest CLIP model, on top of the files below
ExtraDiskSpaceRequired=12884901888
AppMutex=MetascanLauncher
CloseApplications=no
OutputDir=build\output
OutputBaseFilename=Setup
SetupIconFile={#Assets}\metascan.ico
UninstallDisplayIcon={app}\installer\metascan.ico
UninstallDisplayName={#AppName}
WizardStyle=modern
WizardImageFile={#Assets}\wizard.bmp
WizardSmallImageFile={#Assets}\wizard-small.bmp
LicenseFile={#Payload}\LICENSE
Compression=lzma2/max
SolidCompression=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
WelcomeLabel2=This will install [name/ver] on your computer.%n%nSetup also installs what Metascan needs to run and helps you choose your library folders. Plan on about 12 GB of disk space and 10 to 30 minutes, most of it downloading.%n%nYour library files are never moved or changed by Setup.

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "startup"; Description: "Start Metascan in the &tray when I sign in"; GroupDescription: "Shortcuts:"; Flags: unchecked

[InstallDelete]
; An update must not leave modules a newer version removed.
Type: filesandordirs; Name: "{app}\backend"
Type: filesandordirs; Name: "{app}\metascan"
Type: filesandordirs; Name: "{app}\frontend\dist"
Type: filesandordirs; Name: "{app}\installer"

[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Excludes: "\data\t2i_captions\*"; Flags: recursesubdirs ignoreversion createallsubdirs
; The caption lists are the owner's to edit, so an update adds and never replaces.
Source: "{#Payload}\data\t2i_captions\*"; DestDir: "{app}\data\t2i_captions"; Flags: recursesubdirs ignoreversion createallsubdirs onlyifdoesntexist uninsneveruninstall
Source: "{#Assets}\metascan.ico"; DestDir: "{app}\installer"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: "{#LauncherPy}"; WorkingDir: "{app}"; IconFilename: "{app}\installer\metascan.ico"; Comment: "Open your Metascan library"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: "{#LauncherPy}"; WorkingDir: "{app}"; IconFilename: "{app}\installer\metascan.ico"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: "{#LauncherPy} --background"; WorkingDir: "{app}"; IconFilename: "{app}\installer\metascan.ico"; Tasks: startup

[Run]
Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: "{#LauncherPy}"; WorkingDir: "{app}"; Description: "Start Metascan now"; Flags: postinstall nowait skipifsilent; Check: ProvisionSucceeded

[UninstallDelete]
; Made after the copy, so Setup does not know them. The library data is the
; question in CurUninstallStepChanged.
Type: filesandordirs; Name: "{app}\venv"
Type: filesandordirs; Name: "{app}\backend"
Type: filesandordirs; Name: "{app}\metascan"
Type: filesandordirs; Name: "{app}\installer"
Type: filesandordirs; Name: "{app}\frontend"
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
const
  TICK_MS = 300;
  DRIVE_REMOTE = 4;

var
  PrereqPage: TInputOptionWizardPage;
  PrereqNote: TNewStaticText;
  LibraryPage: TWizardPage;
  LibraryList: TNewCheckListBox;
  ComfyPage: TInputQueryWizardPage;
  ComfyResult: TNewStaticText;
  OriginalPage: TInputDirWizardPage;
  ProvisionPage: TOutputProgressWizardPage;
  ProvisionStop: TNewButton;

  HasPython, HasFfmpeg, HasExifTool, HasWinget: Boolean;
  GpuName: String;
  PythonIndex, FfmpegIndex, ExifIndex, ClipIndex: Integer;

  StatusPath, CancelPath: String;
  TimerId: LongWord;
  Provisioned: Boolean;
  ProvisionWarnings: String;
  ProvisionError: String;

function SetTimer(hWnd, nIDEvent, uElapse, lpTimerFunc: LongWord): LongWord;
  external 'SetTimer@user32.dll stdcall';
function KillTimer(hWnd, nIDEvent: LongWord): Boolean;
  external 'KillTimer@user32.dll stdcall';
function GetVolumeInformation(lpRootPathName, lpVolumeNameBuffer: String;
  nVolumeNameSize: LongWord; var lpVolumeSerialNumber, lpMaximumComponentLength,
  lpFileSystemFlags: LongWord; lpFileSystemNameBuffer: String;
  nFileSystemNameSize: LongWord): Boolean;
  external 'GetVolumeInformationW@kernel32.dll stdcall';
function GetDriveType(lpRootPathName: String): LongWord;
  external 'GetDriveTypeW@kernel32.dll stdcall';

function IsUpdate: Boolean;
begin
  Result := FileExists(AddBackslash(WizardDirValue) + 'config.json');
end;

function CommandWorks(const CommandLine: String): Boolean;
var
  Code: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'), '/C ' + CommandLine, '', SW_HIDE,
    ewWaitUntilTerminated, Code) and (Code = 0);
end;

function CommandOutputLine(const CommandLine: String): String;
var
  Code: Integer;
  OutFile: String;
  Lines: TArrayOfString;
begin
  Result := '';
  OutFile := ExpandConstant('{tmp}\probe.txt');
  if Exec(ExpandConstant('{cmd}'), '/C ' + CommandLine + ' > "' + OutFile + '" 2>nul', '',
    SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0)
    and LoadStringsFromFile(OutFile, Lines) and (GetArrayLength(Lines) > 0) then
    Result := Trim(Lines[0]);
end;

procedure DetectPrerequisites;
begin
  HasPython := CommandWorks('py -3.11 -c "import sys"')
    or FileExists(ExpandConstant('{localappdata}\Programs\Python\Python311\python.exe'));
  HasFfmpeg := CommandWorks('where ffmpeg') and CommandWorks('where ffprobe');
  HasExifTool := CommandWorks('where exiftool');
  HasWinget := CommandWorks('where winget')
    or FileExists(ExpandConstant('{#WingetFolder}\winget.exe'));
  GpuName := CommandOutputLine('nvidia-smi --query-gpu=name --format=csv,noheader');
end;

function ParamList(const Name: String): TArrayOfString;
var
  Value: String;
  Part: Integer;
begin
  SetArrayLength(Result, 0);
  Value := ExpandConstant('{param:' + Name + '|}');
  while Value <> '' do
  begin
    Part := Pos(';', Value);
    SetArrayLength(Result, GetArrayLength(Result) + 1);
    if Part = 0 then
    begin
      Result[GetArrayLength(Result) - 1] := Value;
      Value := '';
    end
    else
    begin
      Result[GetArrayLength(Result) - 1] := Copy(Value, 1, Part - 1);
      Delete(Value, 1, Part);
    end;
  end;
end;

{ ---- Paths ---------------------------------------------------------------- }

function PathKey(const Path: String): String;
begin
  Result := Lowercase(AddBackslash(ExpandFileName(Path)));
end;

function PathsOverlap(const A, B: String): Boolean;
var
  KeyA, KeyB: String;
begin
  KeyA := PathKey(A);
  KeyB := PathKey(B);
  Result := (Pos(KeyA, KeyB) = 1) or (Pos(KeyB, KeyA) = 1);
end;

function DataFolder: String;
begin
  Result := AddBackslash(WizardDirValue) + 'data';
end;

function DefaultOriginal: String;
begin
  Result := DataFolder + '\_original';
end;

function FileSystemOf(const Path: String): String;
var
  Serial, MaxLength, Flags: LongWord;
  Volume, FsName: String;
begin
  Volume := StringOfChar(#0, 261);
  FsName := StringOfChar(#0, 261);
  if GetVolumeInformation(AddBackslash(ExtractFileDrive(Path)), Volume, 260, Serial,
    MaxLength, Flags, FsName, 260) then
    Result := Copy(FsName, 1, Pos(#0, FsName) - 1)
  else
    Result := '';
end;

function FolderIsWritable(const Path: String): Boolean;
var
  Probe: String;
  Created: Boolean;
begin
  Created := not DirExists(Path);
  Result := ForceDirectories(Path);
  if Result then
  begin
    Probe := AddBackslash(Path) + 'metascan-write-test.tmp';
    Result := SaveStringToFile(Probe, 'test', False);
    DeleteFile(Probe);
  end;
  if Created then
    RemoveDir(Path);
end;

{ ---- Pages ---------------------------------------------------------------- }

procedure AddLibraryFolder(const Path: String; Subfolders: Boolean);
var
  I: Integer;
begin
  for I := 0 to LibraryList.Items.Count - 1 do
    if PathKey(LibraryList.ItemCaption[I]) = PathKey(Path) then
      Exit;
  LibraryList.AddCheckBox(Path, '', 0, Subfolders, True, False, False, nil);
end;

procedure LibraryAddClick(Sender: TObject);
var
  Folder: String;
begin
  Folder := '';
  if not BrowseForFolder('Choose a folder of images or videos.', Folder, False) then
    Exit;
  if PathsOverlap(Folder, DataFolder) then
    MsgBox('That folder overlaps Metascan''s own data folder:' + #13#10 + DataFolder + #13#10#13#10
      + 'Choose a folder that holds only your media.', mbError, MB_OK)
  else
    AddLibraryFolder(Folder, True);
end;

procedure LibraryRemoveClick(Sender: TObject);
var
  Removed, I, Count: Integer;
  Captions: TArrayOfString;
  Ticks: array of Boolean;
begin
  Removed := LibraryList.ItemIndex;
  if Removed < 0 then
    Exit;
  { The list has no way to drop one row, so it is built again without it. }
  Count := LibraryList.Items.Count;
  SetArrayLength(Captions, Count);
  SetArrayLength(Ticks, Count);
  for I := 0 to Count - 1 do
  begin
    Captions[I] := LibraryList.ItemCaption[I];
    Ticks[I] := LibraryList.Checked[I];
  end;
  LibraryList.Items.Clear;
  for I := 0 to Count - 1 do
    if I <> Removed then
      AddLibraryFolder(Captions[I], Ticks[I]);
end;

function CleanComfyHost: String;
begin
  Result := Trim(ComfyPage.Values[0]);
  StringChangeEx(Result, 'http://', '', True);
  StringChangeEx(Result, 'https://', '', True);
  while (Result <> '') and (Result[Length(Result)] = '/') do
    SetLength(Result, Length(Result) - 1);
end;

function ComfyPortValue: Integer;
begin
  Result := StrToIntDef(Trim(ComfyPage.Values[1]), 0);
end;

procedure ComfyTestClick(Sender: TObject);
var
  Request: Variant;
begin
  if (CleanComfyHost = '') or (ComfyPortValue < 1) or (ComfyPortValue > 65535) then
  begin
    ComfyResult.Caption := 'Enter a host and a port from 1 to 65535.';
    Exit;
  end;
  ComfyResult.Caption := 'Connecting...';
  ComfyResult.Refresh;
  try
    Request := CreateOleObject('WinHttp.WinHttpRequest.5.1');
    Request.SetTimeouts(2000, 2000, 2000, 2000);
    Request.Open('GET', 'http://' + CleanComfyHost + ':' + IntToStr(ComfyPortValue)
      + '/system_stats', False);
    Request.Send('');
    if Request.Status = 200 then
      ComfyResult.Caption := 'Connected to ComfyUI.'
    else
      ComfyResult.Caption := 'Something answered there, but it does not look like ComfyUI.';
  except
    ComfyResult.Caption :=
      'Not reachable right now. That is fine if ComfyUI is not running: Metascan connects when it is.';
  end;
end;

procedure CreatePrereqPage;
begin
  PrereqPage := CreateInputOptionPage(wpSelectDir, 'Prerequisites',
    'Metascan needs these programs. Setup can install what is missing.',
    'Programs already on this PC are ticked and greyed out.', False, False);
  PythonIndex := PrereqPage.Add('');
  FfmpegIndex := PrereqPage.Add('');
  ExifIndex := PrereqPage.Add('');
  ClipIndex := PrereqPage.Add('Download the search model now (up to 4 GB; otherwise on first use)');
  PrereqPage.Values[ClipIndex] := True;

  PrereqNote := TNewStaticText.Create(PrereqPage);
  PrereqNote.Parent := PrereqPage.Surface;
  PrereqNote.AutoSize := False;
  PrereqNote.WordWrap := True;
  PrereqNote.Left := 0;
  PrereqNote.Width := PrereqPage.SurfaceWidth;
  PrereqNote.Top := PrereqPage.CheckListBox.Top + ScaleY(116);
  PrereqNote.Height := ScaleY(84);
  PrereqPage.CheckListBox.Height := ScaleY(108);
end;

procedure SetPrereqRow(Index: Integer; const Name: String; Present: Boolean);
begin
  if Present then
  begin
    PrereqPage.CheckListBox.ItemCaption[Index] := Name + ' (already installed)';
    PrereqPage.Values[Index] := True;
    PrereqPage.CheckListBox.ItemEnabled[Index] := False;
  end
  else
  begin
    PrereqPage.CheckListBox.ItemCaption[Index] := Name;
    PrereqPage.Values[Index] := True;
    PrereqPage.CheckListBox.ItemEnabled[Index] := HasWinget;
  end;
end;

procedure RefreshPrereqPage;
var
  Note: String;
begin
  SetPrereqRow(PythonIndex, 'Python 3.11, installed beside any other Python', HasPython);
  SetPrereqRow(FfmpegIndex, 'FFmpeg, for video thumbnails', HasFfmpeg);
  SetPrereqRow(ExifIndex, 'ExifTool, for video metadata (Windows will ask for permission)', HasExifTool);

  if GpuName <> '' then
    Note := 'Found ' + GpuName + '. Setup installs the GPU build of PyTorch that matches your driver.'
  else
    Note := 'No NVIDIA GPU found, so Metascan will run on the CPU. Search and tagging will be slower.';
  if not HasWinget then
    Note := Note + #13#10#13#10 + 'Windows Package Manager (winget) was not found, so Setup may not be '
      + 'able to install these for you. Install "App Installer" from the Microsoft Store if it fails.'
  else if not (HasPython and HasFfmpeg and HasExifTool) then
    Note := Note + #13#10#13#10 + 'Installing these accepts their publishers'' licences.';
  PrereqNote.Caption := Note;
end;

procedure CreateLibraryPage;
var
  Info: TNewStaticText;
  Add, Remove: TNewButton;
begin
  LibraryPage := CreateCustomPage(PrereqPage.ID, 'Your library',
    'Choose the folders that hold your images and videos.');

  Info := TNewStaticText.Create(LibraryPage);
  Info.Parent := LibraryPage.Surface;
  Info.AutoSize := False;
  Info.WordWrap := True;
  Info.Width := LibraryPage.SurfaceWidth;
  Info.Height := ScaleY(44);
  Info.Caption := 'Metascan reads these folders and watches them for new files. It does not '
    + 'move your files. Tick a folder to include its subfolders. You can change this '
    + 'later in Config, Directories.';

  LibraryList := TNewCheckListBox.Create(LibraryPage);
  LibraryList.Parent := LibraryPage.Surface;
  LibraryList.Top := Info.Top + Info.Height + ScaleY(4);
  LibraryList.Width := LibraryPage.SurfaceWidth - ScaleX(92);
  LibraryList.Height := ScaleY(150);

  Add := TNewButton.Create(LibraryPage);
  Add.Parent := LibraryPage.Surface;
  Add.Caption := '&Add...';
  Add.Left := LibraryPage.SurfaceWidth - ScaleX(84);
  Add.Top := LibraryList.Top;
  Add.Width := ScaleX(84);
  Add.Height := ScaleY(25);
  Add.OnClick := @LibraryAddClick;

  Remove := TNewButton.Create(LibraryPage);
  Remove.Parent := LibraryPage.Surface;
  Remove.Caption := '&Remove';
  Remove.Left := Add.Left;
  Remove.Top := Add.Top + Add.Height + ScaleY(6);
  Remove.Width := Add.Width;
  Remove.Height := Add.Height;
  Remove.OnClick := @LibraryRemoveClick;
end;

procedure CreateComfyPage;
var
  Test: TNewButton;
begin
  ComfyPage := CreateInputQueryPage(LibraryPage.ID, 'ComfyUI',
    'Where does ComfyUI run?',
    'Metascan generates and upscales through ComfyUI. If it runs on this PC with its usual '
    + 'settings, keep these values. Without ComfyUI the rest of Metascan works normally.');
  ComfyPage.Add('&Host:', False);
  ComfyPage.Add('&Port:', False);
  ComfyPage.Values[0] := ExpandConstant('{param:COMFYHOST|127.0.0.1}');
  ComfyPage.Values[1] := ExpandConstant('{param:COMFYPORT|8188}');

  Test := TNewButton.Create(ComfyPage);
  Test.Parent := ComfyPage.Surface;
  Test.Caption := '&Test connection';
  Test.Top := ComfyPage.Edits[1].Top + ComfyPage.Edits[1].Height + ScaleY(10);
  Test.Width := ScaleX(120);
  Test.Height := ScaleY(25);
  Test.OnClick := @ComfyTestClick;

  ComfyResult := TNewStaticText.Create(ComfyPage);
  ComfyResult.Parent := ComfyPage.Surface;
  ComfyResult.AutoSize := False;
  ComfyResult.WordWrap := True;
  ComfyResult.Top := Test.Top + Test.Height + ScaleY(8);
  ComfyResult.Width := ComfyPage.SurfaceWidth;
  ComfyResult.Height := ScaleY(32);
  ComfyResult.Caption := '';
end;

procedure CreateOriginalPage;
begin
  OriginalPage := CreateInputDirPage(ComfyPage.ID, 'Original folder',
    'Where should the originals of upscaled files go?',
    'When you upscale an image or video, Metascan keeps the original here so you can compare '
    + 'or revert. Upscaled video originals can be large, so pick a drive with room. It must be '
    + 'outside your library folders.', False, '');
  OriginalPage.Add('Original folder:');
end;

procedure CreateProvisionPage;
begin
  ProvisionPage := CreateOutputProgressPage('Setting up Metascan',
    'Installing what Metascan needs. This is the long part, and you can use your PC meanwhile.');
  ProvisionStop := TNewButton.Create(ProvisionPage);
  ProvisionStop.Parent := ProvisionPage.Surface;
  ProvisionStop.Caption := 'Stop';
  ProvisionStop.Width := ScaleX(84);
  ProvisionStop.Height := ScaleY(25);
  ProvisionStop.Left := ProvisionPage.SurfaceWidth - ProvisionStop.Width;
  ProvisionStop.Top := ProvisionPage.ProgressBar.Top + ProvisionPage.ProgressBar.Height + ScaleY(16);
end;

procedure ProvisionStopClick(Sender: TObject);
begin
  if SuppressibleMsgBox('Stop setting up Metascan? Metascan will not work until you run Setup again.',
    mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
  begin
    ProvisionStop.Enabled := False;
    SaveStringToFile(CancelPath, 'cancel', False);
  end;
end;

procedure InitializeWizard;
var
  Folders: TArrayOfString;
  I: Integer;
begin
  DetectPrerequisites;
  CreatePrereqPage;
  CreateLibraryPage;
  CreateComfyPage;
  CreateOriginalPage;
  CreateProvisionPage;
  ProvisionStop.OnClick := @ProvisionStopClick;
  RefreshPrereqPage;
  Folders := ParamList('LIBRARY');
  for I := 0 to GetArrayLength(Folders) - 1 do
    AddLibraryFolder(Folders[I], True);
  OriginalPage.Values[0] := ExpandConstant('{param:ORIGINAL|}');
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  { An update keeps config.json: these are all in Config now. }
  Result := IsUpdate and ((PageID = LibraryPage.ID) or (PageID = ComfyPage.ID)
    or (PageID = OriginalPage.ID));
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = OriginalPage.ID) and (OriginalPage.Values[0] = '') then
    OriginalPage.Values[0] := DefaultOriginal;
  if (CurPageID = wpFinished) and not Provisioned then
    WizardForm.FinishedLabel.Caption := 'The files were copied, but Metascan is not ready yet: '
      + ProvisionError + #13#10#13#10 + 'Run Setup again to finish. Details are in '
      + AddBackslash(WizardDirValue) + 'logs\install.log.'
  else if (CurPageID = wpFinished) and (ProvisionWarnings <> '') then
    WizardForm.FinishedLabel.Caption := 'Metascan is installed, but check these:' + #13#10#13#10
      + ProvisionWarnings;
end;

function CheckOriginalFolder: Boolean;
var
  Path: String;
  I: Integer;
begin
  Result := False;
  Path := Trim(OriginalPage.Values[0]);
  if (Length(Path) < 3) or (Path[2] <> ':') and (Copy(Path, 1, 2) <> '\\') then
  begin
    MsgBox('Enter a full folder path, such as D:\Metascan\Originals.', mbError, MB_OK);
    Exit;
  end;
  for I := 0 to LibraryList.Items.Count - 1 do
    if PathsOverlap(Path, LibraryList.ItemCaption[I]) then
    begin
      MsgBox('The Original folder overlaps your library folder:' + #13#10
        + LibraryList.ItemCaption[I] + #13#10#13#10
        + 'Originals must stay out of the library, or Metascan would import them.',
        mbError, MB_OK);
      Exit;
    end;
  if PathsOverlap(Path, DataFolder + '\storyboards') then
  begin
    MsgBox('That folder is where Metascan saves generated files. Choose another.', mbError, MB_OK);
    Exit;
  end;
  Result := True;
end;

function CheckInstallFolder: Boolean;
var
  FileSystem: String;
begin
  Result := False;
  if GetDriveType(AddBackslash(ExtractFileDrive(WizardDirValue))) = DRIVE_REMOTE then
  begin
    MsgBox('Metascan keeps its database here, so it must be on a drive in this PC, '
      + 'not a network drive. Your library can stay on any drive.', mbError, MB_OK);
    Exit;
  end;
  FileSystem := FileSystemOf(WizardDirValue);
  if (FileSystem <> '') and (CompareText(FileSystem, 'NTFS') <> 0) then
    if SuppressibleMsgBox('This drive is formatted ' + FileSystem + '. Metascan''s database is only tested '
      + 'on NTFS, such as your C: drive. Your library can stay on any drive.' + #13#10#13#10
      + 'Install here anyway?', mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDYES) <> IDYES then
      Exit;
  if not FolderIsWritable(WizardDirValue) then
  begin
    MsgBox('Metascan keeps its data next to its program files, and you cannot write to this '
      + 'folder without administrator rights. Choose a folder in your user profile or on '
      + 'another drive.', mbError, MB_OK);
    Exit;
  end;
  Result := True;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpSelectDir then
    Result := CheckInstallFolder
  else if (CurPageID = LibraryPage.ID) and (LibraryList.Items.Count = 0) then
    Result := SuppressibleMsgBox('You have not added a folder, so Metascan will start with an empty '
      + 'library. You can add folders later in Config, Directories.' + #13#10#13#10 + 'Continue?',
      mbConfirmation, MB_YESNO, IDYES) = IDYES
  else if CurPageID = ComfyPage.ID then
  begin
    if (CleanComfyHost = '') or (ComfyPortValue < 1) or (ComfyPortValue > 65535) then
    begin
      MsgBox('Enter a host, such as 127.0.0.1, and a port from 1 to 65535.', mbError, MB_OK);
      Result := False;
    end;
  end
  else if CurPageID = OriginalPage.ID then
    Result := CheckOriginalFolder;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo,
  MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
var
  I: Integer;
  Install: String;
begin
  Result := MemoDirInfo + NewLine + NewLine;
  Install := '';
  if not HasPython and PrereqPage.Values[PythonIndex] then
    Install := Install + Space + 'Python 3.11' + NewLine;
  if not HasFfmpeg and PrereqPage.Values[FfmpegIndex] then
    Install := Install + Space + 'FFmpeg' + NewLine;
  if not HasExifTool and PrereqPage.Values[ExifIndex] then
    Install := Install + Space + 'ExifTool' + NewLine;
  if Install <> '' then
    Result := Result + 'Install:' + NewLine + Install + NewLine;
  if IsUpdate then
    Result := Result + 'Update: your settings and library are kept.' + NewLine + NewLine
  else
  begin
    Result := Result + 'Library folders:' + NewLine;
    if LibraryList.Items.Count = 0 then
      Result := Result + Space + '(none yet)' + NewLine;
    for I := 0 to LibraryList.Items.Count - 1 do
      Result := Result + Space + LibraryList.ItemCaption[I] + NewLine;
    Result := Result + NewLine + 'ComfyUI:' + NewLine + Space + CleanComfyHost + ':'
      + IntToStr(ComfyPortValue) + NewLine + NewLine + 'Original folder:' + NewLine + Space
      + OriginalPage.Values[0] + NewLine + NewLine;
  end;
  Result := Result + MemoTasksInfo;
end;

{ ---- Running provision.ps1 ------------------------------------------------- }

function JsonString(const Value: String): String;
begin
  Result := Value;
  StringChangeEx(Result, '\', '\\', True);
  StringChangeEx(Result, '"', '\"', True);
  Result := '"' + Result + '"';
end;

function WriteSettings(const Path: String): Boolean;
var
  Json, Original: String;
  I: Integer;
  Rows: TArrayOfString;
begin
  Json := '';
  for I := 0 to LibraryList.Items.Count - 1 do
  begin
    if I > 0 then
      Json := Json + ',';
    Json := Json + '{"filepath":' + JsonString(LibraryList.ItemCaption[I])
      + ',"search_subfolders":';
    if LibraryList.Checked[I] then
      Json := Json + 'true}'
    else
      Json := Json + 'false}';
  end;
  { The default stays "", so it follows the install if it ever moves. }
  Original := Trim(OriginalPage.Values[0]);
  if (Original = '') or (PathKey(Original) = PathKey(DefaultOriginal)) then
    Original := '';
  SetArrayLength(Rows, 1);
  Rows[0] := '{"directories":[' + Json + '],"comfy_host":' + JsonString(CleanComfyHost)
    + ',"comfy_port":' + IntToStr(ComfyPortValue) + ',"original_root":' + JsonString(Original) + '}';
  Result := SaveStringsToUTF8File(Path, Rows, False);
end;

procedure ReadProvisionStatus;
var
  Stage, Detail: String;
begin
  Stage := GetIniString('status', 'stage', '', StatusPath);
  if Stage = '' then
    Exit;
  Detail := GetIniString('status', 'detail', '', StatusPath);
  ProvisionPage.SetText(Stage, Detail);
  ProvisionPage.SetProgress(GetIniInt('status', 'percent', 0, 0, 100, StatusPath), 100);
end;

procedure ProvisionTick(H: LongWord; Msg: LongWord; IdEvent: LongWord; Time: LongWord);
begin
  ReadProvisionStatus;
end;

function ProvisionArguments: String;
var
  Script: String;
begin
  Script := ExpandConstant('{app}\installer\provision.ps1');
  Result := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "'
    + Script + '" -AppDir "' + ExpandConstant('{app}') + '" -StatusFile "' + StatusPath
    + '" -CancelFile "' + CancelPath + '"';
  if not IsUpdate then
    Result := Result + ' -Settings "' + ExpandConstant('{tmp}\settings.json') + '"';
  if not HasPython and PrereqPage.Values[PythonIndex] and not WizardSilent then
    Result := Result + ' -InstallPython';
  if not HasFfmpeg and PrereqPage.Values[FfmpegIndex] and not WizardSilent then
    Result := Result + ' -InstallFfmpeg';
  if not HasExifTool and PrereqPage.Values[ExifIndex] and not WizardSilent then
    Result := Result + ' -InstallExifTool';
  if WizardSilent and not (ExpandConstant('{param:NOPREREQS|0}') <> '0') then
    Result := Result + ' -InstallPython -InstallFfmpeg -InstallExifTool';
  if not PrereqPage.Values[ClipIndex] or (ExpandConstant('{param:NOCLIP|0}') <> '0') then
    Result := Result + ' -SkipClip';
  if ExpandConstant('{param:CPUTORCH|0}') <> '0' then
    Result := Result + ' -Torch cpu';
end;

function RunProvision: Boolean;
var
  Code: Integer;
  State: String;
begin
  DeleteFile(StatusPath);
  DeleteFile(CancelPath);
  ProvisionStop.Enabled := True;
  ProvisionPage.SetText('Starting...', '');
  ProvisionPage.SetProgress(0, 100);
  { Exec keeps the window alive while it waits, and a timer brings the news. }
  TimerId := SetTimer(0, 0, TICK_MS, CreateCallback(@ProvisionTick));
  try
    Log('provision.ps1 ' + ProvisionArguments);
    if not Exec('powershell.exe', ProvisionArguments, ExpandConstant('{app}'), SW_HIDE,
      ewWaitUntilTerminated, Code) then
      Code := -1;
  finally
    KillTimer(0, TimerId);
  end;
  ReadProvisionStatus;
  State := GetIniString('status', 'state', '', StatusPath);
  ProvisionWarnings := GetIniString('status', 'warnings', '', StatusPath);
  StringChangeEx(ProvisionWarnings, ' | ', #13#10#13#10, True);
  ProvisionError := GetIniString('status', 'error', '', StatusPath);
  if ProvisionError = '' then
    ProvisionError := 'the setup step stopped unexpectedly.';
  Result := (Code = 0) and (State = 'done');
  Log('provision.ps1 ended: code ' + IntToStr(Code) + ', state ' + State);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Again: Boolean;
begin
  if CurStep <> ssPostInstall then
    Exit;
  StatusPath := ExpandConstant('{tmp}\provision-status.ini');
  CancelPath := ExpandConstant('{tmp}\provision-cancel');
  if not IsUpdate then
    WriteSettings(ExpandConstant('{tmp}\settings.json'));
  ProvisionPage.Show;
  try
    repeat
      Provisioned := RunProvision;
      Again := False;
      if not Provisioned and not FileExists(CancelPath) then
        Again := SuppressibleMsgBox('Setup could not finish:' + #13#10#13#10 + ProvisionError
          + #13#10#13#10 + 'Check your internet connection and try again.',
          mbError, MB_RETRYCANCEL, IDCANCEL) = IDRETRY;
    until not Again;
  finally
    ProvisionPage.Hide;
  end;
end;

function ProvisionSucceeded: Boolean;
begin
  Result := Provisioned;
end;

function GetCustomSetupExitCode: Integer;
begin
  if Provisioned then
    Result := 0
  else
    Result := 1;
end;

{ ---- Uninstall -------------------------------------------------------------- }

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  App: String;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;
  App := ExpandConstant('{app}');
  if UninstallSilent then
    Exit;
  if MsgBox('Metascan is removed. Your media files are untouched.' + #13#10#13#10
    + 'Also delete Metascan''s own data? That is your grades, categories and folders, thumbnails, '
    + 'downloaded models, settings and any originals of upscaled files kept there, in:' + #13#10 + App + #13#10#13#10
    + 'Choose No to keep it for a later install.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    DelTree(App, True, True, True);
end;
