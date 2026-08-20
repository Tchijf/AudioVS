#define MyAppName "Audio Visualizer Studio"
#define MyAppVersion "2.1.0"
#define MyAppPublisher "C△D"
#define MyAppExeName "AudioVisualizerStudio.exe"
#define MyAppId "{B1D87FC2-69A1-4C2F-9EF6-61305EA8B38F}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Setup
VersionInfoProductName={#MyAppName}
VersionInfoVersion=2.1.0.0
VersionInfoProductVersion=2.1.0.0
VersionInfoTextVersion={#MyAppVersion}
DefaultDirName={code:GetDefaultDirName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableReadyPage=yes
OutputDir=..\release
OutputBaseFilename=AudioVisualizerStudio_Setup
SetupIconFile=..\assets\app_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
WizardStyle=modern
WizardImageFile=wizard_large.bmp
WizardSmallImageFile=wizard_small.bmp
WizardImageStretch=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
Compression=lzma2/ultra64
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=no
UsePreviousGroup=yes
UsePreviousTasks=yes
AllowNoIcons=yes
CreateUninstallRegKey=yes
Uninstallable=yes
SetupLogging=yes
ChangesAssociations=no
ChangesEnvironment=no

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "Desktop-Verknüpfung erstellen"; GroupDescription: "Verknüpfungen:"; Flags: checkedonce
Name: "startmenuicon"; Description: "Startmenü-Eintrag erstellen"; GroupDescription: "Verknüpfungen:"; Flags: checkedonce

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README_ENDUSER.txt"; DestDir: "{app}"; DestName: "README.txt"; Flags: ignoreversion

[Icons]
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: startmenuicon
Name: "{autoprograms}\{#MyAppName}\Deinstallieren"; Filename: "{uninstallexe}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: startmenuicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{#MyAppName} jetzt starten"; Flags: nowait postinstall skipifsilent

[Code]
var
  IsUpdating: Boolean;
  PreviousVersion: String;
  DeleteUserData: Boolean;
  PreviousInstallDir: String;

function UninstallKey(): String;
begin
  Result := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppId}_is1';
end;

function ReadPreviousValue(const ValueName: String; var Value: String): Boolean;
var
  Key: String;
begin
  Key := UninstallKey();
  Result := RegQueryStringValue(HKLM64, Key, ValueName, Value);
  if not Result then Result := RegQueryStringValue(HKLM32, Key, ValueName, Value);
  if not Result then Result := RegQueryStringValue(HKCU, Key, ValueName, Value);
end;

function GetDefaultDirName(Param: String): String;
var
  PrevDir: String;
  LegacyRoot: String;
begin
  Result := ExpandConstant('{autopf}\Audio Visualizer Studio');
  if ReadPreviousValue('InstallLocation', PrevDir) and (PrevDir <> '') then
  begin
    LegacyRoot := ExpandConstant('{localappdata}\Programs\AudioVisualizerStudio');
    { v2.0 used LocalAppData. v2.1 deliberately migrates that legacy location to Program Files. }
    if CompareText(RemoveBackslashUnlessRoot(PrevDir), RemoveBackslashUnlessRoot(LegacyRoot)) <> 0 then
      Result := PrevDir;
  end;
end;

function FindPreviousInstall(var Version: String): Boolean;
begin
  Result := ReadPreviousValue('DisplayVersion', Version);
end;

procedure InitializeWizard;
begin
  IsUpdating := FindPreviousInstall(PreviousVersion);
  PreviousInstallDir := '';
  ReadPreviousValue('InstallLocation', PreviousInstallDir);

  if IsUpdating then
    WizardForm.Caption := '{#MyAppName} – Aktualisierung'
  else
    WizardForm.Caption := '{#MyAppName} – Installation';

  if IsUpdating then
  begin
    WizardForm.WelcomeLabel1.Caption := '{#MyAppName} aktualisieren';
    WizardForm.WelcomeLabel2.Caption :=
      'Eine vorhandene Installation (Version ' + PreviousVersion + ') wurde erkannt.' + #13#10 + #13#10 +
      'Der Assistent aktualisiert die Programmdateien. Presets, Autosave, importierte Logos/Cover und persönliche Einstellungen bleiben erhalten.';
  end
  else
  begin
    WizardForm.WelcomeLabel1.Caption := 'Willkommen bei {#MyAppName}';
    WizardForm.WelcomeLabel2.Caption :=
      'Dieser Assistent installiert {#MyAppName} auf deinem PC.' + #13#10 + #13#10 +
      'Python und die Video-Engine sind bereits in der Anwendung enthalten. Nach der Installation sind keine zusätzlichen Downloads erforderlich.';
  end;

  WizardForm.SelectDirLabel.Caption := 'Installationsordner:';
  WizardForm.SelectDirBrowseLabel.Caption := 'Die Anwendung wird standardmäßig in Program Files installiert.';
  if IsUpdating then
    WizardForm.FinishedHeadingLabel.Caption := '{#MyAppName} wurde aktualisiert'
  else
    WizardForm.FinishedHeadingLabel.Caption := '{#MyAppName} ist installiert';
  WizardForm.FinishedLabel.Caption :=
    'Die Anwendung ist startbereit. Persönliche Presets und Einstellungen werden getrennt von den Programmdateien gespeichert.';
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpSelectTasks then
  begin
    WizardForm.PageNameLabel.Caption := 'Desktop & Startmenü';
    WizardForm.PageDescriptionLabel.Caption := 'Wähle die gewünschten Verknüpfungen.';
  end
  else if CurPageID = wpInstalling then
  begin
    if IsUpdating then
      WizardForm.PageNameLabel.Caption := 'Aktualisierung läuft'
    else
      WizardForm.PageNameLabel.Caption := 'Installation läuft';
    WizardForm.PageDescriptionLabel.Caption := 'Audio Visualizer Studio wird eingerichtet. Es sind keine Internet-Downloads erforderlich.';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  NewDir: String;
begin
  if CurStep = ssPostInstall then
  begin
    NewDir := ExpandConstant('{app}');
    if IsUpdating and (PreviousInstallDir <> '') and
       (CompareText(RemoveBackslashUnlessRoot(PreviousInstallDir), RemoveBackslashUnlessRoot(NewDir)) <> 0) and
       FileExists(AddBackslash(PreviousInstallDir) + '{#MyAppExeName}') then
    begin
      { Remove obsolete legacy program files only. User data lives in a separate LocalAppData folder. }
      DelTree(PreviousInstallDir, True, True, True);
    end;
  end;
end;

function InitializeUninstall(): Boolean;
begin
  DeleteUserData :=
    SuppressibleMsgBox(
      'Sollen auch deine eigenen Presets, Autosaves, importierten Logos/Cover und Einstellungen gelöscht werden?' + #13#10 + #13#10 +
      'Standardmäßig bleiben diese Daten erhalten, damit sie bei einer späteren Neuinstallation wieder verfügbar sind.',
      mbConfirmation,
      MB_YESNO or MB_DEFBUTTON2,
      IDNO) = IDYES;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usUninstall) and DeleteUserData then
    DelTree(ExpandConstant('{localappdata}\AudioVisualizerStudio'), True, True, True);
end;
