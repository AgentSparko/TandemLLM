# TandemApp for Homebrew (OPS-234): the controller and its page, without an engine, torch or a GPU.
#
#   brew install ./app/packaging/homebrew/tandemapp.rb  (until a tap carries it)
#   tandem-app service install                          the controller as a launchd agent
#
# Not published: the release sets `url` and `sha256` to its tarball when it ships TandemApp.
class Tandemapp < Formula
  include Language::Python::Virtualenv

  desc "Controller and dashboard for OpenAI-compatible LLM engines and cloud APIs"
  homepage "https://github.com/0xBakeer/TandemLLM"
  url "https://github.com/0xBakeer/TandemLLM/archive/refs/tags/v0.16.0.tar.gz"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  license "AGPL-3.0-only"
  head "https://github.com/0xBakeer/TandemLLM.git", branch: "main"

  depends_on "cryptography"
  depends_on "python@3.13"

  resource "psutil" do
    url "https://files.pythonhosted.org/packages/aa/c6/d1ddf4abb55e93cebc4f2ed8b5d6dbad109ecb8d63748dd2b20ab5e57ebe/psutil-7.2.2.tar.gz"
    sha256 "0746f5f8d406af344fd547f1c8daa5f5c33dbc293bb8d6a16d80b4bb88f59372"
  end

  def install
    # TandemApp is app/ of the repository's tarball (its root once TandemApp ships on its own); the
    # dashboard ships built (dashboard/dist), so no Node at install time
    src = (buildpath/"app/pyproject.toml").exist? ? buildpath/"app" : buildpath
    venv = virtualenv_create(libexec, "python3.13")
    venv.pip_install resources
    venv.pip_install_and_link src
  end

  service do
    run [opt_bin/"tandem-app", "serve", "--host", "127.0.0.1", "--port", "8080"]
    keep_alive true
    working_dir var
    log_path var/"log/tandemapp.log"
    error_log_path var/"log/tandemapp.log"
  end

  def caveats
    <<~EOS
      Run the controller in the background and open its page:
        tandem-app service install --open
      (or `brew services start tandemapp`; use one of the two, not both).
      The page opens on Add Spark while the fleet has no Spark.
      A Mac that sleeps stops the controller and the API: App settings moves it to a Spark.
      Remove the service with:
        tandem-app service uninstall
    EOS
  end

  test do
    assert_match "tandemapp", shell_output("#{bin}/tandem-app version")
    system libexec/"bin/python", "-c", "import cryptography, psutil, proxy.appservice"
  end
end
