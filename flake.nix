{
  description = "Next-generation CLI for ProtonVPN";

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
          ]);
          
          engine = pkgs.buildGoModule {
            pname = "protonvpn-engine";
            version = "1.0.0";
            src = ./engine;
            vendorHash = "sha256-gzwD2uSRAIkFpBnoDMsH/jD8OLU1+Dr70R2u1+lZVCo=";
            buildPhase = ''
              go build -o protonvpn-engine helper.go setup_linux.go
            '';
            installPhase = ''
              mkdir -p $out/bin
              cp protonvpn-engine $out/bin/
            '';
          };
          
        in
        {
          default = pkgs.stdenv.mkDerivation {
            pname = "protonvpn-next-cli";
            version = "1.0.0";

            src = ./.;

            buildInputs = [ pythonEnv pkgs.makeWrapper ];

            buildPhase = ''
              echo "Skipping make build"
            '';

            installPhase = ''
              install -d $out/lib/protonvpn-next
              install -d $out/lib/protonvpn-next/engine
              install -d $out/lib/protonvpn-next/protonvpn_cli
              install -d $out/bin

              install -m 755 protonvpn-next $out/lib/protonvpn-next/protonvpn-next
              cp -r protonvpn_cli/* $out/lib/protonvpn-next/protonvpn_cli/

              install -m 755 ${engine}/bin/protonvpn-engine $out/lib/protonvpn-next/engine/protonvpn-engine
              
              ln -sf $out/lib/protonvpn-next/protonvpn-next $out/bin/protonvpn-next

              wrapProgram $out/lib/protonvpn-next/protonvpn-next \
                --prefix PATH : ${nixpkgs.lib.makeBinPath [ pkgs.sudo ]} \
                --set PYTHONPATH "${pythonEnv}/${pkgs.python3.sitePackages}"
            '';

            meta = {
              mainProgram = "protonvpn-next";
            };
          };
        });
    };
}
