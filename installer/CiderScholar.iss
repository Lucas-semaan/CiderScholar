#ifndef SourceRoot
  #error SourceRoot must point to the verified release staging directory
#endif
#ifndef AppVersion
  #error AppVersion must be provided by the build script
#endif
#ifndef OutputDir
  #error OutputDir must be provided by the build script
#endif

[Setup]
AppId={{AB102B94-65A5-4E38-BA9F-E6EF8A79D084}
AppName=CiderScholar
AppVersion={#AppVersion}
AppPublisher=INRAE
DefaultDirName={localappdata}\Programs\CiderScholar
DefaultGroupName=CiderScholar
OutputDir={#OutputDir}
OutputBaseFilename=CiderScholar-{#AppVersion}-windows-x64
Compression=lzma2/fast
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\runtime\pythonw.exe
WizardStyle=modern
SetupLogging=yes
UsePreviousAppDir=yes
UsePreviousTasks=yes

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; GroupDescription: "Raccourcis :"

[Dirs]
Name: "{localappdata}\CiderScholar\UserData\data\common"
Name: "{localappdata}\CiderScholar\UserData\data\private"
Name: "{localappdata}\CiderScholar\UserData\data\queue"
Name: "{localappdata}\CiderScholar\UserData\data\exports"
Name: "{localappdata}\CiderScholar\UserData\data\backups"
Name: "{localappdata}\CiderScholar\UserData\data\secrets"
Name: "{localappdata}\CiderScholar\UserData\data\runtime"
Name: "{localappdata}\CiderScholar\UserData\data\logs"

[InstallDelete]
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\frontend"
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\scripts"
Type: files; Name: "{app}\LICENSE"
Type: files; Name: "{app}\requirements-runtime.txt"

[Files]
Source: "{#SourceRoot}\application\*"; DestDir: "{app}"; Flags: ignoreversion comparetimestamp recursesubdirs createallsubdirs
Source: "{#SourceRoot}\runtime\*"; DestDir: "{app}\runtime"; Flags: ignoreversion comparetimestamp recursesubdirs createallsubdirs
Source: "{#SourceRoot}\config.runtime.yaml"; DestDir: "{localappdata}\CiderScholar\UserData"; DestName: "config.yaml"; Flags: onlyifdoesntexist
Source: "{#SourceRoot}\models\*"; DestDir: "{localappdata}\CiderScholar\UserData\data\models"; Flags: onlyifdoesntexist recursesubdirs createallsubdirs
Source: "{#SourceRoot}\common-corpus\*"; DestDir: "{localappdata}\CiderScholar\UserData\data\common"; Flags: ignoreversion recursesubdirs createallsubdirs; Check: ShouldInstallBundledCommonCorpus

[Icons]
Name: "{group}\CiderScholar"; Filename: "{app}\runtime\pythonw.exe"; Parameters: "-m scripts.launch_windows"; WorkingDir: "{app}"
Name: "{autodesktop}\CiderScholar"; Filename: "{app}\runtime\pythonw.exe"; Parameters: "-m scripts.launch_windows"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\runtime\pythonw.exe"; Parameters: "-m scripts.launch_windows"; WorkingDir: "{app}"; Description: "Lancer CiderScholar"; Flags: nowait postinstall skipifsilent

[Code]
var
  RemoveUserData: Boolean;
  InstallBundledCommonCorpus: Boolean;

function ShouldInstallBundledCommonCorpus(): Boolean;
begin
  Result := InstallBundledCommonCorpus;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssInstall then
  begin
    InstallBundledCommonCorpus :=
      not FileExists(ExpandConstant('{localappdata}\CiderScholar\UserData\data\common\.ciderscholar-bundled-corpus')) and
      not FileExists(ExpandConstant('{localappdata}\CiderScholar\UserData\data\common\installed.json')) and
      not FileExists(ExpandConstant('{localappdata}\CiderScholar\UserData\data\common\database\science_rag.sqlite3')) and
      not DirExists(ExpandConstant('{localappdata}\CiderScholar\UserData\data\common\qdrant\collection'));
  end;
  if CurStep = ssPostInstall then
  begin
    if not Exec(ExpandConstant('{app}\runtime\python.exe'),
      '-B -m scripts.verify_desktop_install --config "' +
      ExpandConstant('{localappdata}\CiderScholar\UserData\config.yaml') + '"',
      ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
      RaiseException('Le runtime ou un modèle local installé n''a pas passé la vérification d''intégrité.');
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  BackupPath: String;
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    RemoveUserData := SuppressibleMsgBox(
      'Voulez-vous aussi supprimer les conversations, travaux, documents privés et secrets locaux ?' + #13#10 +
      'Choisissez Non pour conserver toutes les données en vue d''une réinstallation.',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES;
    if RemoveUserData then
    begin
      if MsgBox('Créer d''abord une sauvegarde des conversations et documents privés dans Documents ?',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON1) = IDYES then
      begin
        BackupPath := ExpandConstant('{userdocs}\CiderScholar-sauvegarde-avant-desinstallation.zip');
        if not Exec(ExpandConstant('{app}\runtime\python.exe'),
          '-m scripts.backup_before_uninstall --config "' +
          ExpandConstant('{localappdata}\CiderScholar\UserData\config.yaml') +
          '" --destination "' + BackupPath + '"', ExpandConstant('{app}'), SW_HIDE,
          ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
        begin
          MsgBox('La sauvegarde a échoué. Les données locales sont conservées.', mbError, MB_OK);
          RemoveUserData := False;
        end;
      end;
      if RemoveUserData then
        DelTree(ExpandConstant('{localappdata}\CiderScholar\UserData'), True, True, True);
    end;
  end;
end;
