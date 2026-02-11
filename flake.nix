{
  description = "Telegram MCP Server development environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python = pkgs.python313;
      in
      {
        devShells.default = pkgs.mkShell {
          buildInputs = with pkgs; [
            # Python environment
            python
            uv

            # Linting & formatting (pinned via nixpkgs)
            python313Packages.black
            python313Packages.flake8
            python313Packages.pytest
            python313Packages.pytest-asyncio

            # Docker tools
            docker
            docker-compose

            # Development utilities
            git
            curl
            jq

            # For testing HTTP endpoints
            httpie

            # HTTPS tunnel for exposing local server
            cloudflared

            # Deployment
            flyctl
          ];

          shellHook = ''
            echo "🔗 Telegram MCP development environment"
            echo ""
            echo "Available tools:"
            echo "  python3   - Python 3.13"
            echo "  uv        - Fast Python package manager"
            echo "  black     - Code formatter (pinned via nix)"
            echo "  flake8    - Linter (pinned via nix)"
            echo "  pytest    - Test runner (pinned via nix)"
            echo ""
            echo "Quick start:"
            echo "  uv sync                    # Install Python dependencies"
            echo "  uv run python main.py      # Run the MCP server (stdio)"
            echo "  black --check .            # Check formatting"
            echo "  pytest -v                  # Run tests"
            echo "  docker-compose up --build  # Build and run in Docker"
            echo ""
          '';
        };

        # Package for building the Docker image via Nix (optional)
        packages.docker = pkgs.dockerTools.buildImage {
          name = "telegram-mcp";
          tag = "latest";

          copyToRoot = pkgs.buildEnv {
            name = "telegram-mcp-root";
            paths = with pkgs; [
              python313
              cacert
            ];
            pathsToLink = [ "/bin" "/etc" ];
          };

          config = {
            Cmd = [ "${pkgs.python313}/bin/python" "main.py" ];
            WorkingDir = "/app";
            Env = [
              "PYTHONDONTWRITEBYTECODE=1"
              "PYTHONUNBUFFERED=1"
              "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
            ];
          };
        };
      }
    );
}
