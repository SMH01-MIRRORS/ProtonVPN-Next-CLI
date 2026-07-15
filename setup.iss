[Setup]
AppName=ProtonVPN-Next CLI
AppVersion=1.0.0
DefaultDirName={pf}\ProtonVPN-Next CLI
DefaultGroupName=ProtonVPN-Next CLI
UninstallDisplayIcon={app}\pvpn-next-windows.exe
Compression=lzma2
SolidCompression=yes
OutputDir=dist
OutputBaseFilename=pvpn-next-setup
PrivilegesRequired=admin

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"; LicenseFile: "disclaimer-en.txt"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"; LicenseFile: "disclaimer-ru.txt"

[Messages]
english.LicenseLabel3=Please read the following Unofficial Software Notice. You must accept this notice to continue.
english.LicenseAccepted=I understand and agree that this is unofficial software
english.LicenseNotAccepted=I do not agree

russian.LicenseLabel3=Пожалуйста, ознакомьтесь со следующим уведомлением о неофициальном характере ПО. Вы должны принять эти условия, чтобы продолжить установку.
russian.LicenseAccepted=Я прочитал уведомление и согласен с тем, что это неофициальное ПО
russian.LicenseNotAccepted=Я не согласен

[CustomMessages]
english.envPathDesc=Add PVPN-Next CLI to system PATH (recommended)
russian.envPathDesc=Добавить PVPN-Next CLI в системную переменную PATH (рекомендуется)

english.addFirewallDesc=Add Windows Defender Firewall rules for the CLI and Engine
russian.addFirewallDesc=Добавить правила брандмауэра Windows для CLI и Engine

english.enableDaemonDesc=Enable background update daemon
russian.enableDaemonDesc=Включить фоновый демон обновлений

english.autostartDaemonDesc=Start background watchdog service on Windows logon
russian.autostartDaemonDesc=Запускать фоновую службу watchdog при входе в Windows (автозагрузка)

english.installObfuscationDesc=Configure default AmneziaWG obfuscation preset
russian.installObfuscationDesc=Настроить профиль обфускации AmneziaWG по умолчанию (vpn-next-default)

[Files]
Source: "dist\pvpn-next-windows\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Tasks]
Name: envPath; Description: "{cm:envPathDesc}"; Flags: checkedonce
Name: addFirewall; Description: "{cm:addFirewallDesc}"; Flags: checkedonce
Name: enableDaemon; Description: "{cm:enableDaemonDesc}"; Flags: checkedonce
Name: autostartDaemon; Description: "{cm:autostartDaemonDesc}"; Flags: checkedonce
Name: installObfuscation; Description: "{cm:installObfuscationDesc}"; Flags: checkedonce

[Run]
; Run firewall configuration if task selected
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""PVPN-Next CLI"" dir=in action=allow program=""{app}\pvpn-next-windows.exe"" enable=yes"; Flags: runhidden; Tasks: addFirewall
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""PVPN-Next Engine"" dir=in action=allow program=""{app}\engine\pvpn-engine.exe"" enable=yes"; Flags: runhidden; Tasks: addFirewall

; Enable/disable daemon settings in database by running the CLI command
Filename: "{app}\pvpn-next-windows.exe"; Parameters: "daemon on"; Flags: runhidden; Tasks: enableDaemon
Filename: "{app}\pvpn-next-windows.exe"; Parameters: "daemon off"; Flags: runhidden; Tasks: not enableDaemon

; Configure watchdog service autostart by running watchdog task registration
Filename: "{app}\pvpn-next-windows.exe"; Parameters: "_install-watchdog"; Flags: runhidden; Tasks: autostartDaemon

; Configure default AmneziaWG obfuscation preset if selected
Filename: "{app}\pvpn-next-windows.exe"; Parameters: "awg-config set vpn-next-default"; Flags: runhidden; Tasks: installObfuscation

[Registry]
; Run watchdog on Windows logon (startup) if task selected
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "PVPN-Next Watchdog"; ValueData: """{app}\pvpn-next-windows.exe"" _watchdog"; Flags: uninsdeletevalue; Tasks: autostartDaemon

[Code]
const
  EnvironmentKey = 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment';

#ifdef UNICODE
  #define AW "W"
#else
  #define AW "A"
#endif

function SendMessageTimeout(HWND: HWND; Msg: UINT; wParam: Longint; lParam: String; fuFlags: UINT; uTimeout: UINT; var lpdwResult: DWORD): Longint;
  external 'SendMessageTimeout{#AW}@user32.dll stdcall';

procedure AddToPath(PathToAdd: string);
var
  OldPath: string;
  NewPath: string;
  dwResult: DWORD;
begin
  if not RegQueryStringValue(HKEY_LOCAL_MACHINE, EnvironmentKey, 'Path', OldPath) then
    OldPath := '';
    
  // Check if already in Path
  if Pos(StringChange(PathToAdd, '\', '\\'), StringChange(OldPath, '\', '\\')) = 0 then
  begin
    NewPath := OldPath;
    if (NewPath <> '') and (NewPath[Length(NewPath)] <> ';') then
      NewPath := NewPath + ';';
    NewPath := NewPath + PathToAdd;
    if RegWriteStringValue(HKEY_LOCAL_MACHINE, EnvironmentKey, 'Path', NewPath) then
    begin
      // Broadcast settings change to notify system
      SendMessageTimeout(HWND_BROADCAST, $001A, 0, 'Environment', $0002, 5000, dwResult);
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and IsTaskSelected('envPath') then
  begin
    AddToPath(ExpandConstant('{app}'));
  end;
end;
