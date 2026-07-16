{
  description = "Next-generation CLI for PVPN";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      supportedSystems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
      pkgsFor = system: import nixpkgs { inherit system; };
    in
    {
      packages = forAllSystems (system:
        let
          pkgs = pkgsFor system;
          pythonEnv = pkgs.python3.withPackages (ps: with ps; [
            cryptography
            babel
            bcrypt
            sentry-sdk
          ]);
          
          engine = pkgs.buildGoModule {
            pname = "pvpn-engine";
            version = "1.0.0";
            src = ./engine;
            vendorHash = "sha256-BzJXExy7ivS6YQ+NB7vTNFwspsUPpDVW/4n/NvMHlQw=";
            buildPhase = ''
              go build -o pvpn-engine helper.go setup_linux.go
            '';
            installPhase = ''
              mkdir -p $out/bin
              cp pvpn-engine $out/bin/
            '';
          };
          
        in
        {
          default = pkgs.stdenv.mkDerivation {
            pname = "pvpn-next-cli";
            version = "1.0.0";

            src = ./.;

            buildInputs = [ pythonEnv pkgs.makeWrapper ];

            buildPhase = ''
              echo "Skipping make build"
            '';

            installPhase = ''
              install -d $out/lib/pvpn-next
              install -d $out/lib/pvpn-next/engine
              install -d $out/lib/pvpn-next/pvpn_cli
              install -d $out/bin

              install -m 755 pvpn-next $out/lib/pvpn-next/pvpn-next
              cp -r pvpn_cli/* $out/lib/pvpn-next/pvpn_cli/

              install -m 755 ${engine}/bin/pvpn-engine $out/lib/pvpn-next/engine/pvpn-engine
              
              ln -sf $out/lib/pvpn-next/pvpn-next $out/bin/pvpn-next

              wrapProgram $out/lib/pvpn-next/pvpn-next \
                --prefix PATH : ${nixpkgs.lib.makeBinPath [ pkgs.sudo ]} \
                --set PYTHONPATH "${pythonEnv}/${pkgs.python3.sitePackages}"
            '';

            meta = {
              mainProgram = "pvpn-next";
            };
          };
        });
    };
}
