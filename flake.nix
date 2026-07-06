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
          ]);
        in
        {
          default = pkgs.stdenv.mkDerivation {
            pname = "protonvpn-next-cli";
            version = "12.0.0-alpha2";

            src = ./.;

            buildInputs = [ pythonEnv pkgs.go pkgs.makeWrapper ];

            buildPhase = ''
              make build
            '';

            installPhase = ''
              make install DESTDIR=$out PREFIX=""
              
              # Wrap the python executable to ensure it finds its dependencies
              wrapProgram $out/lib/protonvpn-next/protonvpn-next \
                --prefix PATH : ${nixpkgs.lib.makeBinPath [ pkgs.sudo ]} \
                --set PYTHONPATH "${pythonEnv}/lib/python3.11/site-packages"
            '';
          };
        });
    };
}
