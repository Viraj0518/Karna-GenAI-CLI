class Nellie < Formula
  desc "Karna's AI coding agent -- runs in your terminal"
  homepage "https://github.com/Viraj0518/Karna-GenAI-CLI"
  url "https://github.com/Viraj0518/Karna-GenAI-CLI/archive/refs/tags/v0.1.0.tar.gz"
  # sha256 will be filled when the release tarball is published.
  # Generate with: curl -sL <url> | shasum -a 256
  sha256 "PLACEHOLDER_SHA256"
  license "LicenseRef-Proprietary"
  head "https://github.com/Viraj0518/Karna-GenAI-CLI.git", branch: "main"

  depends_on "python@3.12"

  # Optional: git is needed at install time for dev builds,
  # but Homebrew already ensures it.

  def install
    # Create a Python virtualenv and install into it.
    venv = virtualenv_create(libexec, "python3.12")
    venv.pip_install_and_link buildpath

    # Ensure the nellie entrypoint is linked into bin/
    # pip_install_and_link handles this via [project.scripts] in pyproject.toml.
  end

  def caveats
    <<~EOS
      Quick start:
        export OPENROUTER_API_KEY=sk-or-v1-...
        nellie model set openrouter:qwen/qwen3-coder
        nellie

      Run 'nellie --help' for all options.
      Full docs: https://github.com/Viraj0518/Karna-GenAI-CLI/blob/main/docs/INSTALL.md
    EOS
  end

  test do
    # Verify the binary exists and responds to --version
    assert_match(/\d+\.\d+/, shell_output("#{bin}/nellie --version"))
  end
end
