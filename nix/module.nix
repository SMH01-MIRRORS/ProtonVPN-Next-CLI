{ self }:
{ config, lib, pkgs, ... }:

let
  cfg = config.services.pvpn-next;
  system = pkgs.stdenv.hostPlatform.system;
  defaultPackage = self.packages.${system}.default;
in
{
  options.services.pvpn-next = {
    enable = lib.mkEnableOption "PVPN Next restricted networking broker";

    package = lib.mkOption {
      type = lib.types.package;
      default = defaultPackage;
      defaultText = lib.literalExpression "self.packages.${system}.default";
      description = "PVPN Next package used by the broker and CLI.";
    };

    users = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      example = [ "alice" ];
      description = "Users allowed to request connect/disconnect through the broker.";
    };
  };

  config = lib.mkIf cfg.enable {
    users.groups.pvpn-next = { };
    users.users = lib.genAttrs cfg.users (_: {
      extraGroups = [ "pvpn-next" ];
    });

    environment.systemPackages = [ cfg.package ];

    systemd.services.pvpn-next-privileged = {
      description = "PVPN Next privileged networking broker";
      wantedBy = [ "multi-user.target" ];
      after = [ "network.target" ];
      serviceConfig = {
        Type = "simple";
        ExecStart = "${cfg.package}/bin/pvpn-next privileged-service";
        User = "root";
        Group = "pvpn-next";
        UMask = "0077";
        RuntimeDirectory = [ "pvpn-next-service" "pvpn-next" ];
        RuntimeDirectoryMode = "0750";
        Restart = "on-failure";
        RestartSec = 2;
        NoNewPrivileges = true;
        PrivateTmp = true;
        ProtectSystem = "strict";
        ProtectHome = false;
        ReadWritePaths = [ "/home" "/run/pvpn-next" "/run/pvpn-next-service" ];
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        RestrictNamespaces = true;
        RestrictRealtime = true;
        LockPersonality = true;
        RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" "AF_INET6" "AF_NETLINK" ];
        CapabilityBoundingSet = [
          "CAP_NET_ADMIN" "CAP_NET_RAW" "CAP_DAC_OVERRIDE" "CAP_CHOWN"
          "CAP_FOWNER" "CAP_KILL" "CAP_SETUID" "CAP_SETGID"
        ];
      };
    };
  };
}
